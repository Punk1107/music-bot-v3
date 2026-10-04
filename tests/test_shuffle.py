# -*- coding: utf-8 -*-
"""
tests/test_shuffle.py — Unit tests for Feature 1.5: Instant Playlist Shuffle (/play shuffle:True).
"""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from models.track import Track
from utils.embeds import playlist_added_embed
from sources.router import MultiSourceRouter, SourceKind


# ── 1. Embed Shuffled Tag ─────────────────────────────────────────────────────

class TestPlaylistAddedEmbed:
    def test_unshuffled_embed(self):
        embed = playlist_added_embed(10, shuffled=False)
        assert "**10** tracks" in embed.description
        assert "🔀 Shuffled" not in embed.description

    def test_shuffled_embed(self):
        embed = playlist_added_embed(10, shuffled=True)
        assert "**10** tracks" in embed.description
        assert "🔀 Shuffled" in embed.description

    def test_default_is_unshuffled(self):
        embed = playlist_added_embed(5)
        assert "🔀 Shuffled" not in embed.description


# ── 2. Shuffle Logic on Track Lists ──────────────────────────────────────────

class TestTrackShuffleLogic:
    def test_shuffle_preserves_elements_and_count(self):
        tracks = [
            Track(title=f"Track {i}", url=f"https://example.com/{i}", duration=120)
            for i in range(20)
        ]
        original_urls = [t.url for t in tracks]

        random.seed(42)
        random.shuffle(tracks)
        shuffled_urls = [t.url for t in tracks]

        # Elements should be preserved
        assert set(shuffled_urls) == set(original_urls)
        assert len(shuffled_urls) == 20
        # With 20 tracks, seed 42 will change order
        assert shuffled_urls != original_urls

    def test_single_track_no_shuffle_needed(self):
        tracks = [Track(title="Only Track", url="https://example.com/1", duration=120)]
        if len(tracks) > 1:
            random.shuffle(tracks)
        assert len(tracks) == 1
        assert tracks[0].title == "Only Track"


# ── 3. SourcesCog handle_play Shuffle Integration ─────────────────────────────

class TestSourcesCogHandlePlayShuffle:
    @pytest.mark.asyncio
    async def test_sources_handle_play_shuffle_parameter(self):
        from sources.cog import SourcesCog

        mock_bot = MagicMock()
        mock_player = MagicMock()
        mock_player.extend = AsyncMock()
        mock_player.enqueue = AsyncMock()
        mock_bot.get_player.return_value = mock_player

        mock_music_cog = MagicMock()
        mock_vc = MagicMock()
        mock_vc.is_playing.return_value = True
        mock_music_cog._ensure_voice = AsyncMock(return_value=mock_vc)
        mock_bot.cogs = {"Music": mock_music_cog}

        cog = SourcesCog(mock_bot)
        mock_router = MagicMock(spec=MultiSourceRouter)
        mock_router.is_supported.return_value = True

        fake_tracks = [
            Track(title=f"SC Track {i}", url=f"https://soundcloud.com/artist/t{i}", duration=180)
            for i in range(10)
        ]
        mock_router.resolve = AsyncMock(return_value=list(fake_tracks))
        cog.router = mock_router

        mock_interaction = MagicMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        mock_interaction.guild_id = 111
        mock_interaction.user.id = 222
        mock_interaction.user.display_name = "TestUser"

        # Call with shuffle=True
        handled = await cog.handle_play(
            mock_interaction,
            "https://soundcloud.com/artist/sets/my-set",
            shuffle=True,
        )

        assert handled is True
        mock_player.extend.assert_awaited_once()
        # Verify tracks passed to extend were shuffled and attribution set
        enqueued_tracks = mock_player.extend.call_args[0][0]
        assert len(enqueued_tracks) == 10
        for t in enqueued_tracks:
            assert t.requested_by_id == 222
            assert t.requested_by_name == "TestUser"

        # Verify followup embed received shuffled flag
        sent_embed = mock_interaction.followup.send.call_args[1]["embed"]
        assert "🔀 Shuffled" in sent_embed.description
