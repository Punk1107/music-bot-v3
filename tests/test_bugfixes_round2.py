# -*- coding: utf-8 -*-
"""
tests/test_bugfixes_round2.py — Regression & verification tests for Round 2 bugfixes.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.i18n import _STRINGS, get_locale_sync, set_locale_cache, supported_locales, t
from core.player import GuildPlayer, LoopMode
from models.track import Track
from utils.error_handler import (
    dj_required_embed,
    rate_limited_embed,
    voice_connection_error_embed,
)


# ── BUG-R2-01: Chapters seek double volume scaling ─────────────────────────────

@pytest.mark.asyncio
async def test_chapters_seek_handler_unity_volume():
    """Verify that seek_to_chapter passes volume=1.0 to build_ffmpeg_options to prevent double-scaling."""
    from chapters.seek_handler import seek_to_chapter

    bot = MagicMock()
    bot.seek = None  # Ensure fallback seek path is used
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(audio_quality="high"))
    import time
    player = GuildPlayer(guild_id=123)
    player.now_playing = Track(
        title="Test",
        url="https://example.com/1",
        stream_url_cache="https://stream.url/audio",
        stream_url_expires=time.monotonic() + 3600,
    )
    player.volume = 0.5
    bot.get_player.return_value = player

    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    vc.stop = MagicMock()
    vc.play = MagicMock()
    vc.guild.id = 123
    bot.voice_clients = [vc]

    chapter = MagicMock()
    chapter.start_sec = 45.0
    chapter.title = "Chapter 2"

    with patch("chapters.seek_handler.AudioEffectsProcessor") as mock_aep_cls, \
         patch("discord.FFmpegPCMAudio") as mock_audio, \
         patch("discord.PCMVolumeTransformer") as mock_transformer:

        mock_aep = mock_aep_cls.return_value
        mock_aep.build_ffmpeg_options.return_value = {"before_options": "-ss 45", "options": "-vn"}

        success = await seek_to_chapter(bot, 123, chapter)
        assert success is True
        mock_aep.build_ffmpeg_options.assert_called_once()
        _, kwargs = mock_aep.build_ffmpeg_options.call_args
        assert kwargs.get("volume") == 1.0
        mock_transformer.assert_called_once_with(mock_audio.return_value, volume=0.5)


# ── BUG-R2-02: Queue jump LoopMode.TRACK loop & idle vc fallback ────────────────

@pytest.mark.asyncio
async def test_queue_jump_clears_history_track():
    """Verify that player.jump_to(index) clears _history_track so LoopMode.TRACK doesn't loop old song."""
    player = GuildPlayer(guild_id=123)
    t1 = Track(title="Song 1", url="https://example.com/1")
    t2 = Track(title="Song 2", url="https://example.com/2")
    t3 = Track(title="Song 3", url="https://example.com/3")

    player._queue = [t2, t3]
    player.now_playing = t1
    player._history_track = t1
    player.loop_mode = LoopMode.TRACK

    jumped = await player.jump_to(1)  # Jump to Song 3 (0-indexed: index 1 is Song 3)
    assert jumped is not None
    assert jumped.title == "Song 3"
    assert player._history_track is None

    # Dequeue under LoopMode.TRACK must now yield Song 3, not the previous Song 1
    next_track = await player.dequeue()
    assert next_track is not None
    assert next_track.title == "Song 3"


@pytest.mark.asyncio
async def test_queue_jump_idle_triggers_play_next():
    """Verify that /jump and do_jump invoke music_cog._play_next when voice_client is idle."""
    from cogs.queue_cog import QueueCog

    bot = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))
    cog = QueueCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 999
    interaction.user.guild_permissions.administrator = True
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    player = GuildPlayer(guild_id=999)
    t1 = Track(title="Song 1", url="https://example.com/1")
    t2 = Track(title="Song 2", url="https://example.com/2")
    player._queue = [t1, t2]
    bot.get_player.return_value = player

    vc = MagicMock()
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    interaction.guild.voice_client = vc

    music_cog = MagicMock()
    music_cog._play_next = AsyncMock()
    bot.get_cog.return_value = music_cog

    await cog.jump.callback(cog, interaction, 2)
    await asyncio.sleep(0.01)

    # Since vc was neither playing nor paused, _play_next should be triggered
    music_cog._play_next.assert_called_once_with(999)


# ── BUG-R2-03: YouTube stream URL domain validation ────────────────────────────

def test_youtube_stream_url_rejects_apex_and_subdomain_pages():
    """Verify that _extract_stream_url rejects all YouTube webpage URLs (apex & subdomains)."""
    from core.youtube import YouTubeExtractor

    yt = YouTubeExtractor()
    rejected_urls = [
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtu.be/dQw4w9WgXcQ",
        "https://gaming.youtube.com/watch?v=dQw4w9WgXcQ",
    ]

    for url in rejected_urls:
        info = {"url": url}
        assert yt._extract_stream_url(info) is None, f"Expected {url} to be rejected"

    # Valid direct media stream URL (e.g. googlevideo or cdn)
    valid_url = "https://rr1---sn-oxun-xx.googlevideo.com/videoplayback?expire=12345"
    info = {"url": valid_url}
    assert yt._extract_stream_url(info) == valid_url


# ── BUG-R2-04: YouTube search limit parameter ──────────────────────────────────

@pytest.mark.asyncio
async def test_youtube_search_supports_limit_parameter():
    """Verify that YouTubeExtractor.search accepts the limit argument without TypeError."""
    from core.youtube import YouTubeExtractor

    yt = YouTubeExtractor()
    with patch.object(yt, "_extract", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = {
            "entries": [{"id": "123", "title": "Result", "webpage_url": "https://youtube.com/watch?v=123"}]
        }

        results = await yt.search("test query", limit=5)
        assert len(results) == 1
        assert results[0].title == "Result"


# ── 9-Language Parity & Placeholder Verification ───────────────────────────────

def test_all_9_locales_exist_and_have_exact_keys():
    """Verify that all 9 supported locales have exactly identical key sets."""
    locales_dir = Path(__file__).parent.parent / "locales"
    expected_locales = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]

    loaded_locales: dict[str, dict[str, str]] = {}
    for loc in expected_locales:
        p = locales_dir / loc / "strings.json"
        assert p.is_file(), f"Locale file {p} does not exist"
        with open(p, "r", encoding="utf-8") as f:
            loaded_locales[loc] = json.load(f)

    en_keys = set(loaded_locales["en"].keys())
    assert len(en_keys) >= 157, f"Expected at least 157 keys in 'en', found {len(en_keys)}"

    for loc in expected_locales:
        loc_keys = set(loaded_locales[loc].keys())
        missing = en_keys - loc_keys
        extra = loc_keys - en_keys
        assert not missing, f"Locale {loc} is missing keys: {missing}"
        assert not extra, f"Locale {loc} has extra keys: {extra}"


def test_all_9_locales_placeholder_parity():
    """Verify that {placeholders} in each translation match the English reference strings."""
    locales_dir = Path(__file__).parent.parent / "locales"
    expected_locales = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]

    with open(locales_dir / "en" / "strings.json", "r", encoding="utf-8") as f:
        en_dict = json.load(f)

    placeholder_re = re.compile(r"\{([a-zA-Z0-9_]+)\}")

    for loc in expected_locales:
        if loc == "en":
            continue
        with open(locales_dir / loc / "strings.json", "r", encoding="utf-8") as f:
            loc_dict = json.load(f)

        for key, en_val in en_dict.items():
            loc_val = loc_dict.get(key, "")
            en_params = set(placeholder_re.findall(en_val))
            loc_params = set(placeholder_re.findall(loc_val))

            assert en_params == loc_params, (
                f"Mismatch in placeholders for locale '{loc}', key '{key}': "
                f"en={en_params} vs {loc}={loc_params}"
            )


def test_new_round2_keys_translate_in_all_locales():
    """Verify that newly added Round 2 translation keys resolve cleanly in all 9 locales."""
    sample_keys = [
        ("autoplay.up_next", {"title": "Test Title"}),
        ("leave.manual", {}),
        ("btn.stop", {}),
        ("queue.playlist_added_count", {"count": 10}),
        ("queue.jumped", {"title": "Test Title", "pos": 2}),
        ("queue.duplicate_mode", {"mode": "allow"}),
        ("queue.lock_locked", {}),
        ("queue.lock_unlocked", {}),
        ("error.dj_required", {}),
        ("error.rate_limited", {"retry_after": "3.5"}),
        ("error.voice_reconnect_failed", {"channel": "Music", "attempts": 3}),
        ("chapters.not_supported", {}),
        ("chapters.none_found", {}),
        ("chapters.jumped", {"time": "01:23"}),
        ("bookmark.deleted", {"name": "favorites"}),
        ("bookmark.loaded", {"name": "favorites", "count": 5, "mode": "appended to"}),
        ("preset.applied", {"name": "party"}),
    ]

    locales = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]

    for loc in locales:
        for key, kwargs in sample_keys:
            res = t(key, loc, **kwargs)
            assert res != key, f"Key '{key}' was not found in locale '{loc}'"
            assert "{" not in res and "}" not in res, f"Unformatted placeholder in '{loc}' for '{key}': {res}"


# ── Error Handler i18n & dj_required_embed ─────────────────────────────────────

def test_dj_required_embed_with_interaction():
    """Verify that dj_required_embed resolves the locale of the interaction's guild."""
    guild_id = 88888
    set_locale_cache(guild_id, "th")

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id

    embed = dj_required_embed(interaction)
    assert embed is not None
    assert "DJ" in embed.description
    assert t("error.dj_required", "th") in embed.description


def test_dj_required_embed_defaults_gracefully():
    """Verify dj_required_embed without arguments defaults to English."""
    embed = dj_required_embed()
    assert embed is not None
    assert "DJ role" in embed.description


def test_voice_connection_error_embed_i18n():
    """Verify voice_connection_error_embed localizes properly."""
    embed_en = voice_connection_error_embed("Music Channel", 3, "en")
    embed_th = voice_connection_error_embed("Music Channel", 3, "th")
    embed_ja = voice_connection_error_embed("Music Channel", 3, "ja")

    assert "Voice Reconnect Failed" in embed_en.title
    assert "Music Channel" in embed_th.description
    assert "3" in embed_th.description
    assert t("error.voice_reconnect_failed", "th", channel="Music Channel", attempts=3) in embed_th.description
    assert t("error.voice_reconnect_failed", "ja", channel="Music Channel", attempts=3) in embed_ja.description


def test_rate_limited_embed_i18n():
    """Verify rate_limited_embed formats retry_after and translates."""
    embed_en = rate_limited_embed(2.5, "en")
    embed_th = rate_limited_embed(2.5, "th")
    embed_ru = rate_limited_embed(2.5, "ru")

    assert "2.5s" in embed_en.description
    assert "2.5" in embed_th.description
    assert "2.5" in embed_ru.description
    assert t("error.rate_limited", "th", retry_after="2.5") in embed_th.description
