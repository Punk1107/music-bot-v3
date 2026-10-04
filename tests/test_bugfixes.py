# -*- coding: utf-8 -*-
"""
tests/test_bugfixes.py — Regression & verification tests for system bugfixes.
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.i18n import t, _STRINGS, supported_locales
from core.player import GuildPlayer
from models.track import Track
from lyrics.parser import LyricLine
from utils.rate_limiter import RateLimiter


# ── BUG-01: Double Defer in sources/cog.py ─────────────────────────────────────

@pytest.mark.asyncio
async def test_sources_handle_play_skips_defer_if_already_done():
    from sources.cog import SourcesCog

    bot = MagicMock()
    cog = SourcesCog(bot)
    cog.router = MagicMock()
    cog.router.is_supported.return_value = True
    cog.router.resolve = AsyncMock(return_value=[Track(title="Test", url="https://soundcloud.com/test/track")])

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.is_done.return_value = True
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.guild_id = 123
    interaction.user.id = 456
    interaction.user.display_name = "Tester"

    music_cog = MagicMock()
    music_cog._ensure_voice = AsyncMock(return_value=MagicMock())
    bot.cogs.get.return_value = music_cog
    player = GuildPlayer(guild_id=123)
    bot.get_player.return_value = player
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))

    handled = await cog.handle_play(interaction, "https://soundcloud.com/test/track")
    assert handled is True
    # Verify defer was NOT called since response.is_done() was True
    interaction.response.defer.assert_not_called()


@pytest.mark.asyncio
async def test_sources_handle_play_defers_if_not_done():
    from sources.cog import SourcesCog

    bot = MagicMock()
    cog = SourcesCog(bot)
    cog.router = MagicMock()
    cog.router.is_supported.return_value = True
    cog.router.resolve = AsyncMock(return_value=[Track(title="Test", url="https://soundcloud.com/test/track")])

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.is_done.return_value = False
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.guild_id = 123
    interaction.user.id = 456
    interaction.user.display_name = "Tester"

    music_cog = MagicMock()
    music_cog._ensure_voice = AsyncMock(return_value=MagicMock())
    bot.cogs.get.return_value = music_cog
    player = GuildPlayer(guild_id=123)
    bot.get_player.return_value = player
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))

    handled = await cog.handle_play(interaction, "https://soundcloud.com/test/track")
    assert handled is True
    interaction.response.defer.assert_called_once()


# ── BUG-02: Parameter passing in MultiSourceRouter.resolve ─────────────────────

@pytest.mark.asyncio
async def test_multisource_router_resolve_accepts_int_max_tracks():
    from sources.router import MultiSourceRouter

    router = MultiSourceRouter()
    # Mocking internal search/get_track to avoid network calls
    router._sc.search = AsyncMock(return_value=[Track(title="Track 1", url="https://example.com/1")])

    res = await router.resolve("scsearch:test song", max_tracks=50)
    assert len(res) == 1
    assert res[0].title == "Track 1"


# ── BUG-03: Dashboard Lyrics API tuple unpacking ──────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_get_lyrics_unpacks_tuple_correctly():
    from dashboard.service import DashboardService

    bot = MagicMock()
    service = DashboardService(bot)
    player = GuildPlayer(guild_id=123)
    player.now_playing = Track(title="Song With Lyrics", url="https://example.com/song", uploader="Artist")
    bot.get_player.return_value = player

    mock_lyrics = (
        [
            LyricLine(start_sec=1.5, timestamp_str="00:01", text="Hello world"),
            LyricLine(start_sec=5.0, timestamp_str="00:05", text="Second line"),
        ],
        False,  # is_auto
    )

    with patch("lyrics.service.LyricsService.get_lyrics", AsyncMock(return_value=mock_lyrics)):
        res = await service.get_lyrics(123)
        assert res["success"] is True
        assert res["title"] == "Song With Lyrics"
        assert res["is_auto"] is False
        assert len(res["lines"]) == 2
        assert res["lines"][0] == {"start": 1.5, "timestamp": "00:01", "text": "Hello world"}
        assert res["lines"][1] == {"start": 5.0, "timestamp": "00:05", "text": "Second line"}


# ── BUG-04: Seek Volume Unity Gain ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seek_service_passes_unity_volume_to_ffmpeg():
    from seek.service import SeekService

    bot = MagicMock()
    service = SeekService(bot)
    player = GuildPlayer(guild_id=123)
    player.volume = 0.5  # Set non-1.0 volume
    player.now_playing = Track(title="Track", url="https://example.com/track")
    player.now_playing.stream_url_cache = "https://stream.url"
    player.now_playing.stream_url_expires = 9999999999.0
    bot.get_player.return_value = player
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(audio_quality="high"))

    guild = MagicMock()
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = True
    guild.voice_client = vc
    bot.get_guild.return_value = guild
    bot.youtube.get_stream_url = AsyncMock(return_value="https://stream.url")

    # Spy on build_ffmpeg_options
    captured_kwargs = {}
    def mock_build(**kwargs):
        captured_kwargs.update(kwargs)
        return {"before_options": "", "options": ""}

    bot.audio_processor.build_ffmpeg_options.side_effect = mock_build

    with patch("discord.FFmpegPCMAudio", MagicMock()), patch("discord.PCMVolumeTransformer", MagicMock()):
        await service.seek_to(123, 30)

    # volume must be 1.0 (unity gain) in build_ffmpeg_options because PCMVolumeTransformer applies player.volume
    assert captured_kwargs.get("volume") == 1.0


# ── BUG-06: Elapsed seconds for zero-duration track ───────────────────────────

def test_player_elapsed_seconds_zero_duration():
    player = GuildPlayer(guild_id=123)
    player.now_playing = Track(title="Live Stream", url="https://example.com/live", duration=0)
    now = datetime.datetime.now(datetime.timezone.utc)
    player.play_start_time = now - datetime.timedelta(seconds=45)

    # Should report 45 seconds elapsed, not 0
    assert player.elapsed_seconds >= 44
    assert player.elapsed_seconds <= 46


# ── BUG-08: Voice reconnect with StageChannel ──────────────────────────────────

@pytest.mark.asyncio
async def test_try_reconnect_supports_stage_channel():
    from cogs.music import MusicCog

    bot = MagicMock()
    cog = MusicCog(bot)
    player = GuildPlayer(guild_id=123)
    player.last_channel_id = 999
    bot.get_player.return_value = player

    guild = MagicMock()
    bot.get_guild.return_value = guild

    # StageChannel mock
    stage_channel = MagicMock(spec=discord.StageChannel)
    stage_channel.id = 999
    stage_channel.connect = AsyncMock(return_value=MagicMock())
    guild.get_channel.return_value = stage_channel

    with patch("config.RECONNECT_ATTEMPTS", 1), patch("config.RECONNECT_BASE_DELAY", 0.001):
        vc = await cog._try_reconnect(123)
        assert vc is not None
        stage_channel.connect.assert_called_once()


# ── BUG-09 & i18n: btn.clear & All 9 Locales Parity ───────────────────────────

def test_btn_clear_exists_in_all_supported_locales():
    for loc in supported_locales():
        val = t("btn.clear", loc)
        assert val != "btn.clear", f"Locale {loc} is missing 'btn.clear'"
        assert isinstance(val, str) and len(val.strip()) > 0


def test_all_9_locales_have_exact_same_keys():
    en_keys = set(_STRINGS["en"].keys())
    for loc in supported_locales():
        loc_keys = set(_STRINGS[loc].keys())
        missing = en_keys - loc_keys
        extra = loc_keys - en_keys
        assert not missing, f"Locale {loc} missing keys: {missing}"
        assert not extra, f"Locale {loc} extra keys: {extra}"


# ── BUG-13: build_crossfade_in_options has -nostdin ────────────────────────────

def test_crossfade_in_options_has_nostdin():
    from core.audio import AudioEffectsProcessor

    proc = AudioEffectsProcessor()
    opts = proc.build_crossfade_in_options(crossfade_secs=3, volume=1.0)
    assert "-nostdin" in opts["before_options"]


# ── BUG-14: Spotify proactive token refresh ───────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_token_proactive_refresh():
    import time
    from core.spotify import SpotifyExtractor

    sp = SpotifyExtractor()
    sp._available = True
    sp._token = "old_token"
    # Token already expired 10 seconds ago
    sp._token_expires_at = time.monotonic() - 10

    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={"access_token": "new_refreshed_token", "expires_in": 3600})

    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_resp)

    token = await sp._get_token(mock_session)
    assert token == "new_refreshed_token"
    assert sp._token == "new_refreshed_token"
    assert sp._token_expires_at > time.monotonic() + 3000


# ── BUG-15: Rate limiter cleanup ──────────────────────────────────────────────

def test_rate_limiter_cleanup_evicts_stale_entries():
    rl = RateLimiter(max_calls=5, window=0.1)
    rl.is_rate_limited(guild_id=1, user_id=10)
    rl.is_rate_limited(guild_id=1, user_id=20)
    assert (1, 10) in rl._windows
    assert (1, 20) in rl._windows

    # Wait for window to expire
    import time
    time.sleep(0.15)

    rl.cleanup()
    assert (1, 10) not in rl._windows
    assert (1, 20) not in rl._windows
