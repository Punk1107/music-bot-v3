# -*- coding: utf-8 -*-
"""
tests/test_voice_voting.py — Unit tests for Feature 3.3: Interactive Voice Voting (Skip, Clear, Shuffle).
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord

from core.player import GuildPlayer
from models.track import Track
from utils.embeds import vote_skip_embed, vote_clear_embed, vote_shuffle_embed, vote_action_embed
from utils.views import VoteSkipView, VoteClearView, VoteShuffleView


class TestVoteThreshold:
    """Test dynamic threshold calculation: ceil(voice_members * 0.5), minimum 1."""

    @pytest.mark.parametrize(
        "members,expected",
        [
            (0, 1),
            (1, 1),
            (2, 1),
            (3, 2),
            (4, 2),
            (5, 3),
            (6, 3),
            (7, 4),
            (8, 4),
            (9, 5),
            (10, 5),
            (11, 6),
            (20, 10),
        ],
    )
    def test_vote_threshold_values(self, members: int, expected: int):
        player = GuildPlayer(guild_id=123)
        assert player.vote_threshold(members) == expected
        assert player.skip_vote_threshold(members) == expected


class TestPlayerVoteSets:
    """Test vote sets management and reset behavior in GuildPlayer."""

    def test_vote_sets_initial_and_add(self):
        player = GuildPlayer(guild_id=123)
        assert len(player.skip_votes) == 0
        assert len(player.clear_votes) == 0
        assert len(player.shuffle_votes) == 0

        player.skip_votes.add(101)
        player.clear_votes.add(102)
        player.shuffle_votes.add(103)

        assert 101 in player.skip_votes
        assert 102 in player.clear_votes
        assert 103 in player.shuffle_votes

    def test_player_reset_clears_all_vote_sets(self):
        player = GuildPlayer(guild_id=123)
        player.skip_votes.update({1, 2, 3})
        player.clear_votes.update({4, 5})
        player.shuffle_votes.update({6})

        player.reset()

        assert len(player.skip_votes) == 0
        assert len(player.clear_votes) == 0
        assert len(player.shuffle_votes) == 0


class TestVoiceVotingEmbeds:
    """Test embed builders for vote skip, clear, and shuffle."""

    def test_vote_clear_embed_progress_and_locale(self):
        embed_en = vote_clear_embed(
            queue_size=15,
            votes={101, 102},
            threshold=4,
            voters=["Alice", "Bob"],
            locale="en",
        )
        assert "Vote Clear" in embed_en.title
        assert "**Queue Size:** 15 tracks" in embed_en.description
        assert "`█████░░░░░` 50%" in embed_en.description
        assert "2/4" in embed_en.description
        assert "✅ Alice" in embed_en.description
        assert "✅ Bob" in embed_en.description
        assert "Waiting..." in embed_en.description

    def test_vote_shuffle_embed_progress_and_locale(self):
        embed_th = vote_shuffle_embed(
            queue_size=8,
            votes={201},
            threshold=2,
            voters=["Somchai"],
            locale="th",
        )
        assert "โหวตสับเปลี่ยนคิว" in embed_th.title
        assert "**Queue Size:** 8 tracks" in embed_th.description
        assert "`█████░░░░░` 50%" in embed_th.description
        assert "1/2" in embed_th.description
        assert "✅ Somchai" in embed_th.description

    def test_vote_action_embed_full_bar(self):
        embed = vote_action_embed(
            action_type="clear",
            details="Queue Size: 5 tracks",
            votes={1, 2, 3},
            threshold=3,
            voters=["U1", "U2", "U3"],
            locale="en",
        )
        assert "`██████████` 100%" in embed.description
        assert "3/3" in embed.description


class TestVoiceVotingViews:
    """Test VoteClearView and VoteShuffleView button interaction and threshold logic."""

    @pytest.mark.asyncio
    async def test_vote_clear_view_interaction(self):
        mock_bot = MagicMock()
        mock_player = MagicMock()
        mock_player.clear_votes = set()
        mock_player.clear = AsyncMock(return_value=5)
        mock_player.undo_push = MagicMock()
        mock_bot.get_player.return_value = mock_player
        mock_bot.db = MagicMock()
        mock_bot.db.clear_queue = AsyncMock()

        mock_vc = MagicMock()
        mock_vc.channel = MagicMock()
        mock_vc.channel.name = "Music VC"

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_vc
        mock_bot.get_guild.return_value = mock_guild

        view = VoteClearView(
            bot=mock_bot,
            guild_id=123,
            threshold=2,
            locale="en",
        )

        mock_msg = AsyncMock()
        mock_msg.edit = AsyncMock()
        view.set_message(mock_msg)

        # 1. Non-voice user attempts to vote
        mock_interaction_outside = MagicMock()
        mock_interaction_outside.guild_id = 123
        mock_interaction_outside.user.id = 1001
        mock_interaction_outside.user.voice = None
        mock_interaction_outside.response.send_message = AsyncMock()

        await view.vote.callback(mock_interaction_outside)
        mock_interaction_outside.response.send_message.assert_awaited_once()
        err_embed = mock_interaction_outside.response.send_message.call_args[1]["embed"]
        assert "Voice" in err_embed.title

        # 2. Voice user votes (1st vote out of 2)
        mock_interaction_v1 = MagicMock()
        mock_interaction_v1.guild_id = 123
        mock_interaction_v1.user.id = 2001
        mock_interaction_v1.user.display_name = "User1"
        mock_interaction_v1.user.voice.channel = mock_vc.channel
        mock_guild.get_member.return_value = mock_interaction_v1.user
        mock_interaction_v1.response.edit_message = AsyncMock()

        await view.vote.callback(mock_interaction_v1)
        assert 2001 in mock_player.clear_votes
        mock_interaction_v1.response.edit_message.assert_awaited()

        # 3. Second user votes (reaching threshold 2)
        mock_interaction_v2 = MagicMock()
        mock_interaction_v2.guild_id = 123
        mock_interaction_v2.user.id = 2002
        mock_interaction_v2.user.display_name = "User2"
        mock_interaction_v2.user.voice.channel = mock_vc.channel
        mock_guild.get_member.return_value = mock_interaction_v2.user
        mock_interaction_v2.response.edit_message = AsyncMock()

        await view.vote.callback(mock_interaction_v2)
        # Threshold reached: clear() called and votes cleared
        mock_player.clear.assert_awaited_once()
        assert len(mock_player.clear_votes) == 0

    @pytest.mark.asyncio
    async def test_vote_shuffle_view_interaction(self):
        mock_bot = MagicMock()
        mock_player = MagicMock()
        mock_player.shuffle_votes = set()
        mock_player.shuffle = AsyncMock()
        mock_player.undo_push = MagicMock()
        mock_bot.get_player.return_value = mock_player

        mock_vc = MagicMock()
        mock_vc.channel = MagicMock()
        mock_vc.channel.name = "Music VC"

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_vc
        mock_bot.get_guild.return_value = mock_guild

        view = VoteShuffleView(
            bot=mock_bot,
            guild_id=123,
            threshold=1,
            locale="en",
        )

        mock_msg = AsyncMock()
        mock_msg.edit = AsyncMock()
        view.set_message(mock_msg)

        mock_interaction = MagicMock()
        mock_interaction.guild_id = 123
        mock_interaction.user.id = 3001
        mock_interaction.user.display_name = "User3"
        mock_interaction.user.voice.channel = mock_vc.channel
        mock_guild.get_member.return_value = mock_interaction.user
        mock_interaction.response.edit_message = AsyncMock()

        await view.vote.callback(mock_interaction)
        # Threshold is 1, so 1 vote triggers shuffle immediately!
        mock_player.shuffle.assert_awaited_once()
        assert len(mock_player.shuffle_votes) == 0
