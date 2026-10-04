# -*- coding: utf-8 -*-
"""
tests/test_seek.py — Unit tests for Features 2.1–2.4:
  - 2.1 Time Seek (/seek <time>)
  - 2.2 Fast-Forward & Rewind (/forward, /rewind, [⏪ -15s], [⏩ +15s])
  - 2.3 Instant Replay (/replay, /restart)
  - 2.4 Seamless Audio Hot-Reload
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from models.track import Track
from models.server_config import ServerConfig
from seek.parser import parse_time_string
from seek.service import SeekService
from seek.cog import SeekCog
from utils.views import MusicControlView


# ── 1. Timestamp Parser Tests ─────────────────────────────────────────────────

class TestTimestampParser:
    @pytest.mark.parametrize(
        "time_str,expected",
        [
            ("90", 90),
            ("90s", 90),
            ("0", 0),
            ("0s", 0),
            ("120", 120),
            ("300s", 300),
        ],
    )
    def test_parse_seconds(self, time_str: str, expected: int):
        assert parse_time_string(time_str) == expected

    @pytest.mark.parametrize(
        "time_str,expected",
        [
            ("1:30", 90),
            ("01:30", 90),
            ("0:45", 45),
            ("00:00", 0),
            ("3:05", 185),
            ("10:00", 600),
            ("59:59", 3599),
        ],
    )
    def test_parse_colon_mm_ss(self, time_str: str, expected: int):
        assert parse_time_string(time_str) == expected

    @pytest.mark.parametrize(
        "time_str,expected",
        [
            ("1:05:30", 3930),
            ("01:00:00", 3600),
            ("00:01:30", 90),
            ("2:00:00", 7200),
            ("01:30:15", 5415),
        ],
    )
    def test_parse_colon_hh_mm_ss(self, time_str: str, expected: int):
        assert parse_time_string(time_str) == expected

    @pytest.mark.parametrize(
        "time_str,expected",
        [
            ("1m 30s", 90),
            ("1m30s", 90),
            ("2m", 120),
            ("1h 10m", 4200),
            ("45s", 45),
            ("1h", 3600),
            ("1h 2m 3s", 3723),
            ("2hours 30minutes", 9000),
            ("1min 15sec", 75),
        ],
    )
    def test_parse_text_units(self, time_str: str, expected: int):
        assert parse_time_string(time_str) == expected

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "",
            "   ",
            None,
            123,
            "abc",
            "-5",
            "-1:30",
            "::",
            "1:2:3:4",
            "1:65",      # seconds >= 60
            "1:60:00",   # minutes >= 60
            "random words",
            "m10s",
        ],
    )
    def test_parse_invalid(self, invalid_input):
        assert parse_time_string(invalid_input) is None


# ── 2. Seek Boundary Clamping & Math ──────────────────────────────────────────

class TestSeekBoundaryAndMath:
    @pytest.fixture
    def mock_service_and_player(self):
        bot = MagicMock()
        service = SeekService(bot)
        player = MagicMock()
        track = Track(
            title="Test Track",
            url="https://youtube.com/watch?v=123",
            duration=180,
        )
        player.now_playing = track
        player.elapsed_seconds = 30
        player._play_seq = 0
        bot.get_player.return_value = player

        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        bot.db.get_server_config = AsyncMock(return_value=ServerConfig(guild_id=123))
        bot.audio_processor.build_ffmpeg_options.return_value = {"before_options": "", "options": ""}
        bot.youtube.get_stream_url = AsyncMock(return_value="https://audio.stream.url")

        return service, player, vc

    @pytest.mark.asyncio
    async def test_seek_clamping_upper(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await service.seek_to(123, 200)
            assert ok is True
            # Duration is 180, clamped to 179
            args, kwargs = service.bot.audio_processor.build_ffmpeg_options.call_args
            assert kwargs["seek_seconds"] == 179

    @pytest.mark.asyncio
    async def test_seek_clamping_lower(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await service.seek_to(123, -10)
            assert ok is True
            args, kwargs = service.bot.audio_processor.build_ffmpeg_options.call_args
            assert kwargs["seek_seconds"] == 0

    @pytest.mark.asyncio
    async def test_forward_math(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        player.elapsed_seconds = 30
        with patch.object(service, "seek_to", new_callable=AsyncMock) as mock_seek:
            mock_seek.return_value = True
            ok, target = await service.forward(123, 15)
            assert ok is True
            assert target == 45
            mock_seek.assert_awaited_once_with(123, 45)

    @pytest.mark.asyncio
    async def test_forward_near_end_clamped(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        player.elapsed_seconds = 175  # track duration is 180
        with patch.object(service, "seek_to", new_callable=AsyncMock) as mock_seek:
            mock_seek.return_value = True
            ok, target = await service.forward(123, 15)
            assert ok is True
            assert target == 179
            mock_seek.assert_awaited_once_with(123, 179)

    @pytest.mark.asyncio
    async def test_rewind_math(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        player.elapsed_seconds = 40
        with patch.object(service, "seek_to", new_callable=AsyncMock) as mock_seek:
            mock_seek.return_value = True
            ok, target = await service.rewind(123, 15)
            assert ok is True
            assert target == 25
            mock_seek.assert_awaited_once_with(123, 25)

    @pytest.mark.asyncio
    async def test_rewind_near_start_clamped(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        player.elapsed_seconds = 10
        with patch.object(service, "seek_to", new_callable=AsyncMock) as mock_seek:
            mock_seek.return_value = True
            ok, target = await service.rewind(123, 15)
            assert ok is True
            assert target == 0
            mock_seek.assert_awaited_once_with(123, 0)

    @pytest.mark.asyncio
    async def test_replay(self, mock_service_and_player):
        service, player, vc = mock_service_and_player
        with patch.object(service, "seek_to", new_callable=AsyncMock) as mock_seek:
            mock_seek.return_value = True
            ok = await service.replay(123)
            assert ok is True
            mock_seek.assert_awaited_once_with(123, 0)


# ── 3. Seamless Hot-Reload & Sequence Protection ──────────────────────────────

class TestHotReloadAndSequence:
    @pytest.fixture
    def setup_playback(self):
        bot = MagicMock()
        service = SeekService(bot)
        player = MagicMock()
        track = Track(
            title="Now Playing Track",
            url="https://youtube.com/watch?v=now",
            duration=240,
        )
        player.now_playing = track
        player.elapsed_seconds = 75
        player._play_seq = 5
        player._np_last_bar_step = 3
        player.queue = [Track(title="Next In Queue", url="https://youtube.com/watch?v=next")]

        bot.get_player.return_value = player

        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        bot.db.get_server_config = AsyncMock(return_value=ServerConfig(guild_id=123))
        bot.audio_processor.build_ffmpeg_options.return_value = {
            "before_options": "-ss 75",
            "options": "-vn",
        }
        bot.youtube.get_stream_url = AsyncMock(return_value="https://audio.stream.url")

        return service, player, vc

    @pytest.mark.asyncio
    async def test_hot_reload_does_not_advance_queue(self, setup_playback):
        service, player, vc = setup_playback
        old_queue_len = len(player.queue)
        old_track = player.now_playing

        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await service.hot_reload(123)
            assert ok is True

            # Sequence must have been bumped to invalidate old after_play
            assert player._play_seq == 6

            # vc.stop() called once
            vc.stop.assert_called_once()

            # vc.play() called with new source
            vc.play.assert_called_once()

            # Track and queue remain unchanged
            assert player.now_playing == old_track
            assert len(player.queue) == old_queue_len

            # Progress bar cache invalidated
            assert player._np_last_bar_step == -1

            # Play start time adjusted to reflect 75s elapsed
            now = datetime.datetime.now(datetime.timezone.utc)
            delta = now - player.play_start_time
            assert abs(delta.total_seconds() - 75) < 2

    @pytest.mark.asyncio
    async def test_stale_callback_ignored(self, setup_playback):
        service, player, vc = setup_playback
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            await service.seek_to(123, 50)

            # Capture the after callback passed to vc.play
            _, kwargs = vc.play.call_args
            after_cb = kwargs["after"]

            # Simulate another seek / sequence bump happening later
            player._play_seq = 999

            # Call the captured callback from the old seek
            music_cog = MagicMock()
            service.bot.cogs.get.return_value = music_cog
            after_cb(None)

            # Because sequence is stale (999 != 6), _play_next is NOT called
            music_cog._play_next.assert_not_called()


# ── 4. MusicControlView Buttons ───────────────────────────────────────────────

class TestMusicControlViewButtons:
    @pytest.fixture
    def mock_view(self):
        bot = MagicMock()
        bot.seek = MagicMock()
        bot.seek.rewind = AsyncMock(return_value=(True, 15))
        bot.seek.forward = AsyncMock(return_value=(True, 45))

        player = MagicMock()
        player.now_playing = Track(title="Song", url="url", duration=180)
        player.__len__.return_value = 2
        player.loop_mode.value = "off"
        player.volume = 1.0
        bot.get_player.return_value = player

        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        view = MusicControlView(bot, guild_id=123)
        return view, bot, player, vc

    def test_buttons_exist_in_row_1(self, mock_view):
        view, bot, player, vc = mock_view
        custom_ids = [c.custom_id for c in view.children if hasattr(c, "custom_id")]
        assert "mb_rewind" in custom_ids
        assert "mb_forward" in custom_ids

        # Exactly 5 buttons in row 1
        row_1 = [c for c in view.children if getattr(c, "row", None) == 1]
        assert len(row_1) == 5
        assert [c.custom_id for c in row_1] == [
            "mb_rewind", "mb_forward", "mb_vol_down", "mb_vol_up", "mb_favorite"
        ]

    def test_sync_buttons_enabled_when_playing(self, mock_view):
        view, bot, player, vc = mock_view
        view._sync_buttons()
        rewind_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_rewind")
        forward_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_forward")
        assert rewind_btn.disabled is False
        assert forward_btn.disabled is False

    def test_sync_buttons_disabled_when_not_playing(self, mock_view):
        view, bot, player, vc = mock_view
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        player.now_playing = None
        view._sync_buttons()
        rewind_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_rewind")
        forward_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_forward")
        assert rewind_btn.disabled is True
        assert forward_btn.disabled is True

    @pytest.mark.asyncio
    async def test_rewind_button_action(self, mock_view):
        view, bot, player, vc = mock_view
        interaction = MagicMock()
        interaction.user.voice.channel = vc.channel
        interaction.response.defer = AsyncMock()
        interaction.response.edit_message = AsyncMock()

        rewind_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_rewind")
        await rewind_btn.callback(interaction)

        interaction.response.defer.assert_awaited_once()
        bot.seek.rewind.assert_awaited_once_with(123, 15)

    @pytest.mark.asyncio
    async def test_forward_button_action(self, mock_view):
        view, bot, player, vc = mock_view
        interaction = MagicMock()
        interaction.user.voice.channel = vc.channel
        interaction.response.defer = AsyncMock()
        interaction.response.edit_message = AsyncMock()

        forward_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_forward")
        await forward_btn.callback(interaction)

        interaction.response.defer.assert_awaited_once()
        bot.seek.forward.assert_awaited_once_with(123, 15)


# ── 5. SeekCog Slash Commands ─────────────────────────────────────────────────

class TestSeekCog:
    @pytest.fixture
    def setup_cog(self):
        bot = MagicMock()
        bot.db.get_server_config = AsyncMock(return_value=ServerConfig(guild_id=123))
        cog = SeekCog(bot)

        player = MagicMock()
        track = Track(title="Song", url="url", duration=180)
        player.now_playing = track
        bot.get_player.return_value = player

        bot.seek = MagicMock()
        bot.seek.seek_to = AsyncMock(return_value=True)
        bot.seek.forward = AsyncMock(return_value=(True, 45))
        bot.seek.rewind = AsyncMock(return_value=(True, 15))
        bot.seek.replay = AsyncMock(return_value=True)

        return cog, bot, player

    @pytest.mark.asyncio
    async def test_seek_command_success(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.seek.callback(cog, interaction, "1:30")

            bot.seek.seek_to.assert_awaited_once_with(123, 90)
            interaction.followup.send.assert_awaited_once()
            call_kwargs = interaction.followup.send.call_args[1]
            embed = call_kwargs["embed"]
            assert "01:30" in embed.description or "1:30" in embed.description

    @pytest.mark.asyncio
    async def test_seek_command_invalid_format(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.seek.callback(cog, interaction, "invalid-time")

            bot.seek.seek_to.assert_not_called()
            interaction.followup.send.assert_awaited_once()
            call_kwargs = interaction.followup.send.call_args[1]
            embed = call_kwargs["embed"]
            assert "Invalid Timestamp" in embed.title

    @pytest.mark.asyncio
    async def test_seek_command_out_of_range(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.seek.callback(cog, interaction, "10:00")  # 600s > 180s duration

            bot.seek.seek_to.assert_not_called()
            interaction.followup.send.assert_awaited_once()
            call_kwargs = interaction.followup.send.call_args[1]
            embed = call_kwargs["embed"]
            assert "Out of Range" in embed.title

    @pytest.mark.asyncio
    async def test_forward_command(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.forward.callback(cog, interaction, 20)

            bot.seek.forward.assert_awaited_once_with(123, 20)
            interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rewind_command(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.rewind.callback(cog, interaction, 10)

            bot.seek.rewind.assert_awaited_once_with(123, 10)
            interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_replay_and_restart_command(self, setup_cog):
        cog, bot, player = setup_cog
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_check_permissions", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            await cog.replay.callback(cog, interaction)
            bot.seek.replay.assert_awaited_once_with(123)

            await cog.restart.callback(cog, interaction)
            assert bot.seek.replay.await_count == 2
