# -*- coding: utf-8 -*-
"""
tests/test_bugfixes_round3.py — Comprehensive tests for Round 3 bug fixes and i18n parity.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.i18n import (
    _STRINGS,
    get_locale,
    get_locale_sync,
    set_locale_cache,
    supported_locales,
    t,
)
from core.player import GuildPlayer, LoopMode
from models.track import Track
from utils.embeds import (
    auto_playlist_embed,
    bookmark_saved_embed,
    dj_cleared_embed,
    dj_set_embed,
    now_playing_embed,
    request_channel_set_embed,
)


# ── 1. Chapters seek audio filter preservation ───────────────────────────────

@pytest.mark.asyncio
async def test_chapters_seek_preserves_audio_filters():
    """Verify seek_to_chapter passes equalizer_filter, loudnorm, pan_filter, stereo_filter."""
    from chapters.seek_handler import seek_to_chapter

    bot = MagicMock()
    bot.seek = None  # Force fallback path in seek_handler
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(audio_quality="high"))

    player = GuildPlayer(guild_id=999)
    player.now_playing = Track(
        title="Filter Test",
        url="https://youtube.com/watch?v=abcdef12345",
        stream_url_cache="https://audio.stream/test",
        stream_url_expires=time.monotonic() + 3600,
    )
    player.equalizer_bands = {"sub_bass": 3.0, "bass": 2.0, "mid": -1.0, "treble": 4.0}
    player.loudnorm = True
    player.pan_balance = -0.5
    player.stereo_width = 1.4
    bot.get_player.return_value = player

    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    vc.guild.id = 999
    bot.get_guild.return_value = MagicMock(voice_client=vc)

    chapter = MagicMock()
    chapter.start_sec = 60.0
    chapter.title = "Chapter 3"

    with patch("chapters.seek_handler.AudioEffectsProcessor") as mock_aep_cls, \
         patch("chapters.seek_handler.discord.FFmpegPCMAudio"), \
         patch("chapters.seek_handler.discord.PCMVolumeTransformer"):
        mock_aep = MagicMock()
        mock_aep_cls.return_value = mock_aep
        mock_aep.build_ffmpeg_options.return_value = {"before_options": "", "options": ""}

        success = await seek_to_chapter(bot, 999, chapter)
        assert success is True
        mock_aep.build_ffmpeg_options.assert_called_once()
        _, kwargs = mock_aep.build_ffmpeg_options.call_args
        assert kwargs.get("loudnorm") is True
        assert kwargs.get("equalizer_filter") is not None
        assert "equalizer" in kwargs.get("equalizer_filter")
        assert kwargs.get("pan_filter") is not None
        assert kwargs.get("stereo_filter") is not None


# ── 2. Chapters voice & permission checks ─────────────────────────────────────

@pytest.mark.asyncio
async def test_chapters_jump_checks_voice_permissions():
    """Verify /chapter_jump rejects users not in voice or in the wrong channel."""
    from chapters.cog import ChaptersCog

    bot = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None, language="en"))
    cog = ChaptersCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    # User not in voice
    member = MagicMock(spec=discord.Member)
    member.voice = None
    interaction.user = member

    await cog._do_jump(interaction, "1:30")
    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Not in Voice" in embed.title or "voice" in embed.description.lower()

    # User in wrong voice channel
    interaction.followup.send.reset_mock()
    user_vc = MagicMock()
    user_vc.name = "User Voice"
    bot_vc_channel = MagicMock()
    bot_vc_channel.name = "Bot Voice"
    member.voice = MagicMock(channel=user_vc)
    guild = MagicMock()
    guild.voice_client = MagicMock(channel=bot_vc_channel)
    interaction.guild = guild

    await cog._do_jump(interaction, "1:30")
    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Wrong Channel" in embed.title or "Bot Voice" in embed.description


@pytest.mark.asyncio
async def test_chapters_dropdown_select_checks_voice():
    """Verify ChapterSelect.callback rejects users not in the voice channel."""
    from chapters.views import ChapterSelect
    from chapters.detector import Chapter

    bot = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))
    select = ChapterSelect(
        bot=bot,
        guild_id=123,
        chapters=[Chapter(index=0, title="Intro", start_sec=0.0, end_sec=30.0, duration_sec=30.0)],
    )
    select._selected_values = ["0"]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    member = MagicMock(spec=discord.Member)
    member.voice = None
    interaction.user = member

    await select.callback(interaction)
    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Not in Voice" in embed.title or "voice" in embed.description.lower()


# ── 3. Sources Cog voice connect safety ───────────────────────────────────────

@pytest.mark.asyncio
async def test_sources_sc_search_select_ensures_voice():
    """Verify SCSearchSelect.callback calls music_cog._ensure_voice(interaction)."""
    from sources.cog import SCSearchSelect

    bot = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))
    player = GuildPlayer(guild_id=123)
    bot.get_player.return_value = player

    music_cog = MagicMock()
    music_cog._ensure_voice = AsyncMock(return_value=None)  # Simulate user cancelled or failed join
    bot.cogs.get.return_value = music_cog

    from sources.cog import SCSearchView

    track = Track(title="SC Track", url="https://soundcloud.com/artist/track")
    view = SCSearchView(bot=bot, guild_id=123, tracks=[track])
    select = view.children[0]
    select._values = ["0"]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.message.edit = AsyncMock()
    member = MagicMock(spec=discord.Member)
    interaction.user = member

    guild = MagicMock()
    guild.voice_client = None
    bot.get_guild.return_value = guild

    await select.callback(interaction)
    music_cog._ensure_voice.assert_called_once_with(interaction)


# ── 4. Playnext SoundCloud empty resolve error handling ───────────────────────

@pytest.mark.asyncio
async def test_playnext_unsupported_or_empty_resolve_shows_error():
    """Verify playnext handles empty resolve without falling through to YouTube."""
    from cogs.music import MusicCog

    bot = MagicMock()
    bot.spotify.is_spotify_url.return_value = False
    bot.youtube.is_playlist_url.return_value = False
    bot.youtube.is_youtube_url.return_value = False
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))
    cog = MusicCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    sources_cog = MagicMock()
    sources_cog.router.is_supported.return_value = True
    sources_cog.router.resolve = AsyncMock(return_value=[])  # Empty resolve (broken link)
    bot.cogs.get.return_value = sources_cog

    await cog.playnext.callback(cog, interaction, "https://soundcloud.com/broken/link")
    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Not Found" in embed.title or "No results" in embed.description or "error" in embed.title.lower()
    # yt_breaker should not have been called!
    bot.yt_breaker.call.assert_not_called()


# ── 5. main.py _np_refresh locale persistence ────────────────────────────────

@pytest.mark.asyncio
async def test_main_np_refresh_passes_guild_locale():
    """Verify now_playing_embed receives the guild's cached locale on periodic tick."""
    from main import MusicBot

    bot = MusicBot()
    set_locale_cache(777, "th")

    player = GuildPlayer(guild_id=777)
    player.now_playing = Track(title="Periodic Refresh Song", url="https://youtube.com/watch?v=123", duration=180)
    player.play_start_time = datetime.datetime.now(datetime.timezone.utc)
    player.now_playing_msg = MagicMock()
    player.now_playing_msg.edit = AsyncMock()
    player._cached_base_color = 0x5865F2
    bot._players[777] = player

    guild = MagicMock()
    guild.voice_client = MagicMock(is_paused=MagicMock(return_value=False))
    bot.get_guild = MagicMock(return_value=guild)

    with patch("utils.embeds.now_playing_embed") as mock_np_embed:
        mock_np_embed.return_value = MagicMock()
        await bot._np_refresh.coro(bot)
        mock_np_embed.assert_called_once()
        _, kwargs = mock_np_embed.call_args
        assert kwargs.get("locale") == "th"


# ── 6. UI views localization ──────────────────────────────────────────────────

def test_music_control_view_buttons_localized():
    """Verify MusicControlView._sync_buttons uses translated strings."""
    from utils.views import MusicControlView

    bot = MagicMock()
    player = GuildPlayer(guild_id=888)
    player.now_playing = Track(title="Song", url="https://example.com")
    bot.get_player.return_value = player
    bot.get_guild.return_value = MagicMock(voice_client=MagicMock(is_paused=lambda: False, is_playing=lambda: True))

    set_locale_cache(888, "th")
    view_th = MusicControlView(bot, guild_id=888)
    labels_th = {child.custom_id: child.label for child in view_th.children if hasattr(child, "custom_id")}
    assert labels_th["mb_skip"] == t("btn.skip", "th")
    assert labels_th["mb_shuffle"] == t("btn.shuffle", "th")
    assert labels_th["mb_pause"] == t("btn.pause", "th")

    set_locale_cache(888, "ja")
    view_ja = MusicControlView(bot, guild_id=888)
    labels_ja = {child.custom_id: child.label for child in view_ja.children if hasattr(child, "custom_id")}
    assert labels_ja["mb_skip"] == t("btn.skip", "ja")
    assert labels_ja["mb_pause"] == t("btn.pause", "ja")


@pytest.mark.asyncio
async def test_queue_view_pagination_preserves_locale():
    """Verify QueueView pagination passes locale to queue_embed."""
    from utils.views import QueueView

    bot = MagicMock()
    player = GuildPlayer(guild_id=444)
    for i in range(15):
        player._queue.append(Track(title=f"Track {i}", url=f"https://example.com/{i}", duration=120))
    bot.get_player.return_value = player

    set_locale_cache(444, "fr")
    view = QueueView(bot, guild_id=444, page=1)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.edit_message = AsyncMock()

    with patch("utils.views.queue_embed") as mock_q_embed:
        mock_q_embed.return_value = MagicMock()
        await view.next_page.callback(interaction)
        mock_q_embed.assert_called_once()
        _, kwargs = mock_q_embed.call_args
        assert kwargs.get("locale") == "fr"


# ── 7. Equalizer and Loudnorm permission errors localized ─────────────────────

@pytest.mark.asyncio
async def test_loudnorm_and_equalizer_permission_errors_localized():
    """Verify _check_permissions uses locale in loudnorm and equalizer cogs."""
    from loudnorm.cog import LoudnormCog
    from equalizer.cog import EqualizerCog

    bot = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))

    ln_cog = LoudnormCog(bot)
    eq_cog = EqualizerCog(bot)

    set_locale_cache(111, "es")

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 111
    interaction.followup.send = AsyncMock()

    # User in wrong channel
    user_vc = MagicMock(name="User Channel")
    user_vc.name = "User Channel"
    bot_vc = MagicMock(name="Bot Channel")
    bot_vc.name = "Bot Channel"
    member = MagicMock(spec=discord.Member)
    member.voice = MagicMock(channel=user_vc)
    interaction.user = member
    interaction.guild = MagicMock(voice_client=MagicMock(channel=bot_vc))

    # Test loudnorm
    res_ln = await ln_cog._check_permissions(interaction)
    assert res_ln is False
    embed_ln = interaction.followup.send.call_args.kwargs["embed"]
    assert embed_ln.description == t("error.wrong_channel", "es", channel="Bot Channel")

    # Test equalizer
    interaction.followup.send.reset_mock()
    res_eq = await eq_cog._check_permissions(interaction)
    assert res_eq is False
    embed_eq = interaction.followup.send.call_args.kwargs["embed"]
    assert embed_eq.description == t("error.wrong_channel", "es", channel="Bot Channel")


# ── 8. Embeds & Volume key parity across all 9 locales ────────────────────────

def test_embeds_volume_key_parity_and_admin_embeds():
    """Verify now_playing_embed, bookmark_saved_embed, and admin embeds."""
    player = GuildPlayer(guild_id=123)
    player.now_playing = Track(title="Volume Test", url="https://example.com", duration=180)
    player.volume = 0.8
    user = MagicMock()
    user.display_name = "TestUser"
    user.display_avatar.url = "https://avatar.url"

    for loc in supported_locales():
        embed = now_playing_embed(player, 0x5865F2, user, locale=loc)
        vol_field = next(f for f in embed.fields if f.name == t("embed.volume", loc))
        assert vol_field is not None
        assert vol_field.value == "80%"

        # Test bookmark saved
        bm_embed = bookmark_saved_embed("MyFavs", 12, locale=loc)
        assert bm_embed.description == t("bookmark.saved", loc, name="MyFavs", count=12)

        # Test admin embeds
        role = MagicMock()
        role.mention = "@DJ"
        role.color.value = 0x5865F2
        dj_embed = dj_set_embed(role, locale=loc)
        assert dj_embed.description == t("admin.dj_set", loc, role="@DJ")

        clr_embed = dj_cleared_embed(locale=loc)
        assert clr_embed.description == t("admin.dj_cleared", loc)

        ch = MagicMock()
        ch.mention = "#music-requests"
        rc_embed = request_channel_set_embed(ch, locale=loc)
        assert rc_embed.description == t("admin.request_channel_set", loc, channel="#music-requests")

        ap_embed = auto_playlist_embed(5, locale=loc)
        assert ap_embed.description == t("admin.auto_playlist", loc, count=5)


# ── 9. Dashboard service queue mutations & SQLite persistence ─────────────────

@pytest.mark.asyncio
async def test_dashboard_service_queue_mutation_db_persistence():
    """Verify move, remove, clear, shuffle in DashboardService persist to DB."""
    from dashboard.service import DashboardService

    bot = MagicMock()
    bot.db.save_queue = AsyncMock()
    bot.db.clear_queue = AsyncMock()

    service = DashboardService(bot)
    service.broadcast_state = AsyncMock()

    player = GuildPlayer(guild_id=555)
    t1 = Track(title="Song 1", url="https://example.com/1")
    t2 = Track(title="Song 2", url="https://example.com/2")
    t3 = Track(title="Song 3", url="https://example.com/3")
    player._queue.extend([t1, t2, t3])
    bot.get_player.return_value = player
    bot.get_guild.return_value = MagicMock(voice_client=MagicMock(channel=MagicMock(id=99)))

    # 1. move_queue
    res = await service.move_queue(555, 0, 2)
    assert res["success"] is True
    bot.db.save_queue.assert_called_once()
    assert [t.title for t in player.queue] == ["Song 2", "Song 3", "Song 1"]

    # 2. remove_from_queue
    bot.db.save_queue.reset_mock()
    res = await service.remove_from_queue(555, 1)
    assert res["success"] is True
    bot.db.save_queue.assert_called_once()
    assert [t.title for t in player.queue] == ["Song 2", "Song 1"]

    # 3. shuffle_queue
    bot.db.save_queue.reset_mock()
    res = await service.shuffle_queue(555)
    assert res["success"] is True
    bot.db.save_queue.assert_called_once()

    # 4. clear_queue
    res = await service.clear_queue(555)
    assert res["success"] is True
    assert res["cleared_count"] == 2
    bot.db.clear_queue.assert_called_once_with(555)
    # Check undo was pushed
    assert len(player.undo_stack) > 0
    assert player.undo_stack[-1].operation == "clear"


# ── 10. Sleep timer clears DB queue on trigger ────────────────────────────────

@pytest.mark.asyncio
async def test_sleep_timer_clears_db_queue_on_fire():
    """Verify that when sleep timer fires, DB queue is cleared."""
    from cogs.sleep_timer_cog import SleepTimerCog

    bot = MagicMock()
    bot.db.clear_queue = AsyncMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None, language="en"))

    cog = SleepTimerCog(bot)
    player = GuildPlayer(guild_id=321)
    bot.get_player.return_value = player

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 321
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = True
    interaction.user = member

    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()
    guild = MagicMock(voice_client=vc)
    bot.get_guild.return_value = guild

    # Set 0s / very short timer to trigger quickly
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await cog.sleep.callback(cog, interaction, "1s")
        assert player.sleep_timer_task is not None
        await player.sleep_timer_task

    bot.db.clear_queue.assert_called_once_with(321)
    vc.disconnect.assert_called_once()


# ── 11. Metrics probe uses db.ping() ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_db_latency_uses_ping():
    """Verify MetricsCollector measures latency with db.ping()."""
    from core.metrics import MetricsCollector

    bot = MagicMock()
    bot.db.ping = AsyncMock(return_value=4.2)
    bot.db.get_server_config = AsyncMock()
    collector = MetricsCollector(bot)

    snap = await collector.collect()
    bot.db.ping.assert_called_once()
    bot.db.get_server_config.assert_not_called()
    assert snap.db_latency_ms == pytest.approx(4.2)


# ── 12. Full 9-locale parity check for Round 3 keys ───────────────────────────

def test_all_9_locales_exact_parity_round3():
    """Verify all 9 locales contain all new Round 3 keys with exact placeholders."""
    new_keys = {
        "embed.volume": set(),
        "bookmark.saved": {"name", "count"},
        "admin.dj_set": {"role"},
        "admin.dj_cleared": set(),
        "admin.request_channel_set": {"channel"},
        "admin.auto_playlist": {"count"},
    }

    locales = supported_locales()
    assert len(locales) == 9

    import re
    placeholder_pattern = re.compile(r"\{([a-zA-Z0-9_]+)\}")

    for loc in locales:
        loc_strings = _STRINGS.get(loc, {})
        for key, expected_params in new_keys.items():
            assert key in loc_strings, f"Key {key!r} missing in locale {loc!r}"
            val = loc_strings[key]
            assert isinstance(val, str) and len(val) > 0, f"Value for {key!r} in {loc!r} is empty"
            found_params = set(placeholder_pattern.findall(val))
            assert found_params == expected_params, (
                f"Locale {loc!r} key {key!r} params {found_params} != {expected_params}"
            )
