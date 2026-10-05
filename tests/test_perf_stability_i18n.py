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


class TestPhase2PerformanceAndStability:
    """Tests for Round 2 Performance optimizations, Stability, and 9-Language Parity."""

    def test_all_9_languages_exact_key_parity(self):
        """All 9 language files must have 100% key parity and identical placeholder variables."""
        import json
        import re
        from pathlib import Path

        locales_dir = Path(__file__).resolve().parent.parent / "locales"
        all_langs = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]

        lang_data = {}
        for lang in all_langs:
            file_path = locales_dir / lang / "strings.json"
            assert file_path.is_file(), f"Missing strings.json for {lang}"
            with open(file_path, "r", encoding="utf-8") as f:
                lang_data[lang] = json.load(f)

        en_keys = set(lang_data["en"].keys())
        placeholder_re = re.compile(r"\{([a-zA-Z0-9_]+)\}")

        for lang in all_langs:
            curr_keys = set(lang_data[lang].keys())
            missing = en_keys - curr_keys
            extra = curr_keys - en_keys
            assert not missing, f"Locale {lang} is missing keys: {missing}"
            assert not extra, f"Locale {lang} has extra un-synced keys: {extra}"

            # Check placeholders
            for key in en_keys:
                en_placeholders = set(placeholder_re.findall(lang_data["en"][key]))
                lang_placeholders = set(placeholder_re.findall(lang_data[lang][key]))
                assert en_placeholders == lang_placeholders, (
                    f"Placeholder mismatch in {lang} for key '{key}': "
                    f"expected {en_placeholders}, got {lang_placeholders}"
                )

    @pytest.mark.asyncio
    async def test_autoplay_recommendation_caching(self):
        """AutoplayService caches recommendations for video_id with TTL and evicts oldest."""
        from autoplay.service import AutoplayService
        from models.track import Track

        mock_yt = MagicMock()
        service = AutoplayService(mock_yt)
        service._cache_ttl = 5.0
        service._cache_max = 3

        # Mock _run_ytdl
        service._run_ytdl = MagicMock(return_value={"entries": [{"id": "rel1", "title": "Related 1", "duration": 200}]})

        # 1. First fetch: cache MISS
        res1 = await service.fetch_related_tracks("vid1", limit=5)
        assert len(res1) == 1
        assert service._run_ytdl.call_count == 1
        assert "vid1" in service._recommendation_cache

        # 2. Second fetch: cache HIT (does not call _run_ytdl)
        res2 = await service.fetch_related_tracks("vid1", limit=5)
        assert len(res2) == 1
        assert service._run_ytdl.call_count == 1

        # 3. Cache TTL expiry
        service._recommendation_cache["vid1"] = (service._recommendation_cache["vid1"][0], time.monotonic() - 10.0)
        res3 = await service.fetch_related_tracks("vid1", limit=5)
        assert len(res3) == 1
        assert service._run_ytdl.call_count == 2

        # 4. Cache max size eviction
        service._run_ytdl.return_value = {"entries": [{"id": "rel2", "title": "Related 2", "duration": 200}]}
        await service.fetch_related_tracks("vid2", limit=5)
        service._run_ytdl.return_value = {"entries": [{"id": "rel3", "title": "Related 3", "duration": 200}]}
        await service.fetch_related_tracks("vid3", limit=5)
        assert len(service._recommendation_cache) == 3
        # Add 4th item, oldest ("vid1") is evicted
        service._run_ytdl.return_value = {"entries": [{"id": "rel4", "title": "Related 4", "duration": 200}]}
        await service.fetch_related_tracks("vid4", limit=5)
        assert len(service._recommendation_cache) == 3
        assert "vid1" not in service._recommendation_cache
        assert "vid4" in service._recommendation_cache

        # 5. clear_cache
        service.clear_cache()
        assert len(service._recommendation_cache) == 0

    def test_rate_limiter_throttled_cleanup(self):
        """RateLimiter throttles cleanup() calls to at most once per 60s when table is large."""
        from utils.rate_limiter import RateLimiter

        rl = RateLimiter(max_calls=5, window=10.0)
        rl._cleanup_interval = 60.0

        # Populate with 1005 keys
        now = time.monotonic()
        for i in range(1005):
            rl._windows[(1, i)].append(now - 20.0)  # expired

        cleanup_called = 0
        original_cleanup = rl.cleanup

        def counted_cleanup():
            nonlocal cleanup_called
            cleanup_called += 1
            original_cleanup()

        rl.cleanup = counted_cleanup

        # First check when table > 1000 triggers cleanup
        rl.is_rate_limited(999, 999)
        assert cleanup_called == 1

        # Immediate next check within 60s must NOT trigger cleanup even if > 1000
        for i in range(1005):
            rl._windows[(2, i)].append(now)

        rl.is_rate_limited(999, 999)
        assert cleanup_called == 1  # Still 1, throttled!

    @pytest.mark.asyncio
    async def test_database_analytics_buffer_hard_cap(self):
        """Analytics buffer never exceeds ANALYTICS_BUF_MAX even on repeated failure."""
        from core.database import DatabaseManager, ANALYTICS_BUF_MAX

        db = DatabaseManager(":memory:")
        assert ANALYTICS_BUF_MAX == 2000

        # Push items past cap
        async with db._analytics_lock:
            for i in range(ANALYTICS_BUF_MAX + 500):
                if len(db._analytics_buf) >= ANALYTICS_BUF_MAX:
                    db._analytics_buf.pop(0)
                db._analytics_buf.append((123, "test_event", "{}"))

            assert len(db._analytics_buf) == ANALYTICS_BUF_MAX

    def test_ffmpeg_pool_acquire_kills_warm_process(self):
        """FFmpegWarmPool.acquire kills process of discarded warm source."""
        from core.ffmpeg_pool import FFmpegWarmPool

        pool = FFmpegWarmPool(pool_size=2)
        mock_source = MagicMock()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_source._process = mock_proc

        pool._ready.put_nowait(mock_source)

        with patch("discord.FFmpegPCMAudio") as mock_ffmpeg_create:
            mock_new_src = MagicMock()
            mock_ffmpeg_create.return_value = mock_new_src

            res = pool.acquire("http://example.com/audio.mp3", {})
            mock_source.cleanup.assert_called_once()
            mock_proc.kill.assert_called_once()
            assert res == mock_new_src

    @pytest.mark.asyncio
    async def test_np_refresh_resilient_error_handling(self):
        """_np_refresh does NOT clear now_playing_msg on transient 429/5xx Discord HTTPException."""
        from main import MusicBot
        from core.player import GuildPlayer
        from models.track import Track

        bot = MagicMock(spec=MusicBot)
        bot.http_session = AsyncMock()
        player = GuildPlayer(12345)
        player._cached_base_color = 0x5865F2
        track = Track(title="Song", url="http://youtube.com/watch?v=1", duration=180)
        player.now_playing = track

        mock_msg = MagicMock()
        mock_msg.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=429), "Rate limit"))
        player.now_playing_msg = mock_msg
        player.now_playing_msg_id = 9999

        bot._players = {12345: player}
        bot.get_guild = MagicMock(return_value=None)
        bot.user = MagicMock()

        # Run _np_refresh loop body
        await MusicBot._np_refresh.coro(bot)

        # On transient 429: now_playing_msg MUST NOT be cleared!
        assert player.now_playing_msg is not None
        assert player.now_playing_msg == mock_msg

        # On 404 NotFound: now_playing_msg MUST be cleared
        mock_msg.edit = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "Not found"))
        await MusicBot._np_refresh.coro(bot)
        assert player.now_playing_msg is None
        assert player.now_playing_msg_id is None

    def test_progress_bar_32_division_knob_movement(self):
        """Progress bar width 32 divides total duration into 32 equal slices and advances knob accurately."""
        from utils.formatters import make_knob_progress_bar

        # 32-minute podcast (1920s): 1 slot = 60s
        total_sec = 1920
        # At 0s: knob at pos 0
        bar0 = make_knob_progress_bar(0 / total_sec, "0:00", "32:00", width=32)
        assert "●" in bar0
        assert bar0.startswith("▶️ ●─────────────────────────────── [0:00/32:00]")

        # At 120s (2 minutes = 2 slots out of 31 steps):
        frac2 = 120 / total_sec  # 0.0625
        bar2 = make_knob_progress_bar(frac2, "2:00", "32:00", width=32)
        assert "──●" in bar2

        # At half (960s = 16 minutes):
        frac_half = 960 / total_sec
        bar_half = make_knob_progress_bar(frac_half, "16:00", "32:00", width=32)
        assert ("─" * 16 + "●") in bar_half

