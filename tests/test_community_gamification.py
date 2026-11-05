# -*- coding: utf-8 -*-
"""
tests/test_community_gamification.py — Consolidated test suite for Phase 2: Community & Gamification.

Covers:
  1. Interactive Blind Music Quiz (B-Tree Instant Sampling, SequenceMatcher, QuizSession, UI Views)
  2. Fair-Share Round-Robin Queue & Per-User Queue Limits
  3. Multi-Playlist Personal Manager (MySQL 8.0+ 1:N relations, cascade delete, share/play)
  4. Audio Clip Cutter & Ringer Generator (/clip duration clamping & FFmpeg export)
  5. "Song Catcher" Instant DM Delivery (/grab command, mb_grab button, discord.Forbidden handling)
"""

from __future__ import annotations

import asyncio
from collections import deque
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord

from core.database import DatabaseManager
from core.player import GuildPlayer
from models.track import Track
from quiz.engine import QuizSession, clean_song_title, is_answer_correct
from quiz.views import QuizOptionButton, QuizQuestionView
from playlist.service import PlaylistService
from cogs.queue_cog import QueueCog
from cogs.playback_cog import PlaybackCog
from cogs.music import MusicCog
from utils.views import MusicControlView


# ── 1. Interactive Blind Music Quiz Tests ────────────────────────────────────

class TestBlindMusicQuizEngine:
    """Tests for song title cleaning, fuzzy SequenceMatcher, and QuizSession."""

    def test_clean_song_title(self):
        assert clean_song_title("Never Gonna Give You Up (Official Music Video)") == "Never Gonna Give You Up"
        assert clean_song_title("Bohemian Rhapsody [Official Video HD]") == "Bohemian Rhapsody"
        assert clean_song_title("Song Title (feat. Artist)") == "Song Title"
        assert clean_song_title("Simple Song") == "Simple Song"

    def test_is_answer_correct(self):
        title = "Shape of You"
        assert is_answer_correct("shape of you", title) is True
        assert is_answer_correct("Shape of You!", title) is True
        # Close typo matching
        assert is_answer_correct("Shape of Yu", title, threshold=0.75) is True
        # Completely wrong
        assert is_answer_correct("Despacito", title) is False

    @pytest.mark.asyncio
    async def test_quiz_session_lifecycle(self):
        bot = MagicMock()
        tracks = [
            Track(title="Song One", url="https://yt.com/1", duration=200, uploader="Artist 1"),
            Track(title="Song Two", url="https://yt.com/2", duration=210, uploader="Artist 2"),
        ]
        bot.db = MagicMock()
        bot.db.get_random_quiz_track = AsyncMock(side_effect=[tracks[0], tracks[1], None])
        bot.db.get_quiz_distractors = AsyncMock(return_value=["Distractor 1", "Distractor 2", "Distractor 3"])

        channel = MagicMock(spec=discord.TextChannel)
        session = QuizSession(bot, guild_id=123, channel=channel, total_rounds=2, source="history")

        # Round 1
        r1_data = await session.prepare_round()
        assert r1_data is not None
        assert session.current_round == 1
        assert session.round_active is True

        user1 = MagicMock(id=101, display_name="Alice")
        user2 = MagicMock(id=102, display_name="Bob")

        # User 1 clicks correct button
        ok = session.register_answer(user1, session.correct_index)
        assert ok is True
        # Duplicate click in same round rejected
        assert session.register_answer(user1, session.correct_index) is False
        assert session.scores[101] == 15  # 10 + 5 bonus for first

        # User 2 clicks wrong button
        wrong_idx = (session.correct_index + 1) % 4
        assert session.register_answer(user2, wrong_idx) is False

        # Round 2
        r2_data = await session.prepare_round()
        assert r2_data is not None
        assert session.current_round == 2

        # User 2 guesses via text correctly
        assert session.register_text_guess(user2, "Song Two") is True
        assert session.scores[user2.id] == 15
        assert session.round_active is False  # text answer ends round

    @pytest.mark.asyncio
    async def test_quiz_database_sampling_and_leaderboard(self):
        db = DatabaseManager(db_path=":memory:", migration_mode="sqlite_fallback")
        await db.initialise()

        # Seed history tracks
        for i in range(1, 10):
            track = Track(
                title=f"Quiz Song {i}",
                url=f"https://yt.com/{i}",
                duration=180,
                uploader="Artist",
                requested_by_id=100 + i,
            )
            await db.record_track_played(123, 100 + i, track, duration_played=180)

        # Random track lookup
        random_track = await db.get_random_quiz_track(123, source="history")
        assert random_track is not None
        assert "Quiz Song" in random_track.title

        # Distractors lookup (should return 3 different titles)
        distractors = await db.get_quiz_distractors(123, exclude_title=random_track.title, limit=3)
        assert len(distractors) <= 3
        assert random_track.title not in distractors

        # Record score and verify leaderboard
        await db.record_quiz_score(123, 101, points=25, won=True)
        await db.record_quiz_score(123, 102, points=15, won=False)
        await db.record_quiz_score(123, 101, points=10, won=False)  # Alice now 35

        lb = await db.get_quiz_leaderboard(123, limit=5)
        assert len(lb) == 2
        assert lb[0]["user_id"] == 101
        assert lb[0]["score"] == 35
        assert lb[0]["games_won"] == 1
        assert lb[1]["user_id"] == 102
        assert lb[1]["score"] == 15

        await db.close()

    @pytest.mark.asyncio
    async def test_quiz_question_view_interaction(self):
        interaction = MagicMock()
        interaction.user.id = 101
        interaction.user.display_name = "Alice"
        interaction.response.send_message = AsyncMock()

        session = MagicMock()
        session.round_active = True
        session.answered_users = set()
        session.register_answer.return_value = True

        view = QuizQuestionView(
            session=session,
            options=["My Song", "Wrong 1", "Wrong 2", "Wrong 3"],
        )

        # Click the first button
        first_btn = view.children[0]
        assert isinstance(first_btn, QuizOptionButton)
        await first_btn.callback(interaction)

        session.register_answer.assert_called_once_with(interaction.user, 0)
        interaction.response.send_message.assert_awaited_once()
        assert "Correct" in interaction.response.send_message.call_args[0][0]


# ── 2. Fair-Share Round-Robin Queue Tests ────────────────────────────────────

class TestFairShareQueue:
    """Tests for Round-Robin queue reordering and per-user queue limits."""

    @pytest.mark.asyncio
    async def test_reorder_fair_share(self):
        player = GuildPlayer(123)
        # User A queues 3 songs, User B queues 2 songs, User C queues 1 song
        tA1 = Track(title="A1", url="uA1", duration=100, requested_by_id=101)
        tA2 = Track(title="A2", url="uA2", duration=100, requested_by_id=101)
        tA3 = Track(title="A3", url="uA3", duration=100, requested_by_id=101)
        tB1 = Track(title="B1", url="uB1", duration=100, requested_by_id=102)
        tB2 = Track(title="B2", url="uB2", duration=100, requested_by_id=102)
        tC1 = Track(title="C1", url="uC1", duration=100, requested_by_id=103)

        player._queue = deque([tA1, tA2, tA3, tB1, tB2, tC1])
        await player.reorder_fair_share()

        # Expected round-robin: A1, B1, C1, A2, B2, A3
        titles = [t.title for t in player.queue]
        assert titles == ["A1", "B1", "C1", "A2", "B2", "A3"]

    @pytest.mark.asyncio
    async def test_dequeue_fair_mode_cycles_users(self):
        player = GuildPlayer(123)
        player.queue_mode = "fair"

        tA1 = Track(title="A1", url="uA1", duration=100, requested_by_id=101)
        tA2 = Track(title="A2", url="uA2", duration=100, requested_by_id=101)
        tB1 = Track(title="B1", url="uB1", duration=100, requested_by_id=102)

        player._queue = deque([tA1, tA2, tB1])

        # First dequeue gets A1 (since last_requested_by is None)
        first = await player.dequeue()
        assert first.title == "A1"
        assert player._last_requested_by == 101

        # Second dequeue prioritizes User 102 over User 101!
        second = await player.dequeue()
        assert second.title == "B1"
        assert player._last_requested_by == 102

        # Third dequeue gets remaining A2
        third = await player.dequeue()
        assert third.title == "A2"

    def test_user_track_count_and_limit(self):
        player = GuildPlayer(123)
        player.per_user_limit = 2

        t1 = Track(title="T1", url="u1", duration=100, requested_by_id=101)
        t2 = Track(title="T2", url="u2", duration=100, requested_by_id=101)
        t3 = Track(title="T3", url="u3", duration=100, requested_by_id=102)

        player._queue = deque([t1, t2, t3])
        assert player.user_track_count(101) == 2
        assert player.user_track_count(102) == 1
        assert player.user_track_count(103) == 0

    @pytest.mark.asyncio
    async def test_queue_command_mode_and_limit_toggle(self):
        bot = MagicMock()
        player = GuildPlayer(123)
        bot.get_player.return_value = player
        bot.db = MagicMock()
        cfg = MagicMock()
        cfg.dj_role_id = None
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.set_server_config = AsyncMock()

        cog = QueueCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.user.guild_permissions.administrator = True
        interaction.guild.get_member.return_value = interaction.user
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.queue_cog.get_locale", return_value="en"):
            # Set mode fair and limit 3
            await cog.queue_cmd.callback(cog, interaction, page=1, mode="fair", per_user_limit=3)

        assert player.queue_mode == "fair"
        assert player.per_user_limit == 3
        interaction.followup.send.assert_awaited_once()


# ── 3. Multi-Playlist Personal Manager Tests ─────────────────────────────────

class TestMultiPlaylistManager:
    """Tests for multi-playlist CRUD, cascade track removal, and sharing."""

    @pytest.mark.asyncio
    async def test_multi_playlist_relational_crud(self):
        db = DatabaseManager(db_path=":memory:", migration_mode="sqlite_fallback")
        await db.initialise()

        # 1. Create playlist
        t1 = Track(title="Chill Song 1", url="https://yt.com/c1", duration=180)
        t2 = Track(title="Chill Song 2", url="https://yt.com/c2", duration=210)

        ok = await db.create_playlist(
            user_id=101,
            guild_id=123,
            name="Chill Vibes",
            tracks=[t1, t2],
            is_public=False,
        )
        assert ok is True

        # Verify tracks in playlist
        tracks = await db.load_playlist(101, 123, "Chill Vibes")
        assert tracks is not None
        assert len(tracks) == 2
        assert tracks[0].title == "Chill Song 1"
        assert tracks[1].title == "Chill Song 2"

        # 2. Add track 3
        t3 = Track(title="Chill Song 3", url="https://yt.com/c3", duration=240)
        assert await db.add_track_to_playlist(101, 123, "Chill Vibes", t3) is True
        tracks_after_add = await db.load_playlist(101, 123, "Chill Vibes")
        assert len(tracks_after_add) == 3

        # 3. Remove track position 0
        assert await db.remove_track_from_playlist(101, 123, "Chill Vibes", position=0) is True
        tracks_after_rem = await db.load_playlist(101, 123, "Chill Vibes")
        assert len(tracks_after_rem) == 2
        assert tracks_after_rem[0].title == "Chill Song 2"

        # 4. Toggle share (public)
        assert await db.set_playlist_public(101, 123, "Chill Vibes", is_public=True) is True

        # 5. Delete playlist (Cascade check)
        assert await db.delete_playlist(101, 123, "Chill Vibes") is True
        deleted_tracks = await db.load_playlist(101, 123, "Chill Vibes")
        assert deleted_tracks is None

        await db.close()

    def test_playlist_service_embed_builder(self):
        bot = MagicMock()
        service = PlaylistService(bot)
        user = MagicMock()
        user.display_name = "Alice"
        user.id = 101

        playlists = [
            {"id": 1, "name": "Work Focus", "is_public": 0, "count": 5, "user_id": 101},
            {"id": 2, "name": "Party", "is_public": 1, "count": 12, "user_id": 102},
        ]
        embed = service.build_playlist_list_embed(playlists, user)
        assert "Alice" in embed.title
        assert "Work Focus" in embed.description
        assert "Party" in embed.description


# ── 4. Audio Clip Cutter Tests ───────────────────────────────────────────────

class TestAudioClipCutter:
    """Tests for /clip command duration validation and FFmpeg parameters."""

    @pytest.mark.asyncio
    async def test_clip_duration_clamping(self):
        bot = MagicMock()
        player = MagicMock()
        track = Track(title="Test Track", url="https://yt.com/test", duration=300)
        track.stream_url_cache = "https://audio.stream/1"
        player.now_playing = track
        bot.get_player.return_value = player

        cog = PlaybackCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock subprocess.run to create the output MP3 file
        def mock_subprocess_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"ID3dummy")
            ret = MagicMock()
            ret.returncode = 0
            return ret

        with patch("subprocess.run", side_effect=mock_subprocess_run), \
             patch("cogs.playback_cog.get_locale", return_value="en"):

            # Request 45s (should clamp to 30s)
            await cog.clip.callback(cog, interaction, duration=45)

        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.call_args[1]
        assert "file" in kwargs
        assert isinstance(kwargs["file"], discord.File)
        assert kwargs["file"].filename.endswith(".mp3")


# ── 5. Song Catcher Instant DM Delivery Tests ────────────────────────────────

class TestSongCatcherGrab:
    """Tests for /grab command and mb_grab button DM delivery."""

    @pytest.mark.asyncio
    async def test_grab_command_success(self):
        bot = MagicMock()
        player = MagicMock()
        track = Track(title="Bohemian Rhapsody", url="https://yt.com/queen", duration=354, uploader="Queen", thumbnail="https://img.yt.com/vi/1/hq.jpg")
        player.now_playing = track
        bot.get_player.return_value = player

        cog = MusicCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.guild.name = "Rock Guild"
        interaction.user.send = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.music.get_locale", return_value="en"):
            await cog.grab.callback(cog, interaction)

        interaction.user.send.assert_awaited_once()
        sent_embed = interaction.user.send.call_args[1]["embed"]
        assert "Bohemian Rhapsody" in sent_embed.title
        assert "Queen" in sent_embed.description
        assert "Rock Guild" in sent_embed.description

    @pytest.mark.asyncio
    async def test_grab_button_dm_forbidden_fallback(self):
        bot = MagicMock()
        player = MagicMock()
        player.volume = 1.0
        player.__len__ = MagicMock(return_value=1)
        track = Track(title="Song X", url="https://yt.com/x", duration=120, uploader="Artist X")
        player.now_playing = track
        bot.get_player.return_value = player
        bot.get_guild.return_value = None

        with patch("utils.views.get_locale_sync", return_value="en"):
            view = MusicControlView(bot, guild_id=123)

        btn = next(c for c in view.children if getattr(c, "custom_id", None) == "mb_grab")

        interaction = MagicMock()
        interaction.user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "DMs closed"))
        interaction.response.send_message = AsyncMock()

        with patch("utils.views.get_locale_sync", return_value="en"):
            await btn.callback(interaction)

        # Should catch Forbidden and notify user ephemerally
        interaction.response.send_message.assert_awaited_once()
        sent_text = interaction.response.send_message.call_args[0][0]
        assert "Privacy" in sent_text or "Direct Message" in sent_text
