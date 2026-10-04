# -*- coding: utf-8 -*-
"""
tests/test_playnext.py — Unit tests for Feature 3.1: /playnext command & queue insertion.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.player import GuildPlayer
from models.track import Track
from utils.embeds import track_added_embed


class TestGuildPlayerPlayNext:
    """Tests for GuildPlayer.enqueue_next and extend_next."""

    @pytest.mark.asyncio
    async def test_enqueue_next_on_empty_queue(self):
        player = GuildPlayer(guild_id=123)
        track = Track(title="Song A", url="https://example.com/a", duration=180)
        q_len = await player.enqueue_next(track)
        assert q_len == 1
        assert len(player) == 1
        assert player.queue[0] == track

    @pytest.mark.asyncio
    async def test_enqueue_next_inserts_at_head(self):
        player = GuildPlayer(guild_id=123)
        track1 = Track(title="Song 1", url="https://example.com/1", duration=100)
        track2 = Track(title="Song 2", url="https://example.com/2", duration=200)
        track3 = Track(title="Song 3", url="https://example.com/3", duration=300)
        await player.enqueue(track1)
        await player.enqueue(track2)
        assert [t.title for t in player.queue] == ["Song 1", "Song 2"]

        # Insert track 3 as next
        q_len = await player.enqueue_next(track3)
        assert q_len == 3
        assert len(player) == 3
        # Head of the queue is now Song 3
        assert player.queue[0] == track3
        assert [t.title for t in player.queue] == ["Song 3", "Song 1", "Song 2"]

    @pytest.mark.asyncio
    async def test_extend_next_preserves_order_at_head(self):
        player = GuildPlayer(guild_id=123)
        track_existing = Track(title="Existing", url="https://example.com/ex", duration=100)
        await player.enqueue(track_existing)

        batch = [
            Track(title=f"New {i}", url=f"https://example.com/new/{i}", duration=60)
            for i in range(3)
        ]
        q_len = await player.extend_next(batch)
        assert q_len == 4
        assert len(player) == 4
        # Order should be New 0, New 1, New 2, Existing
        expected_titles = ["New 0", "New 1", "New 2", "Existing"]
        assert [t.title for t in player.queue] == expected_titles

    @pytest.mark.asyncio
    async def test_multiple_enqueue_next_stacking(self):
        player = GuildPlayer(guild_id=123)
        t1 = Track(title="First", url="https://example.com/1", duration=100)
        t2 = Track(title="Second", url="https://example.com/2", duration=100)
        t3 = Track(title="Third", url="https://example.com/3", duration=100)
        await player.enqueue(t1)
        await player.enqueue_next(t2)
        await player.enqueue_next(t3)
        # t3 was added last with appendleft, so it is at index 0
        assert [t.title for t in player.queue] == ["Third", "Second", "First"]

    @pytest.mark.asyncio
    async def test_dequeue_after_enqueue_next(self):
        player = GuildPlayer(guild_id=123)
        t1 = Track(title="Normal 1", url="https://example.com/1", duration=100)
        t2 = Track(title="Normal 2", url="https://example.com/2", duration=100)
        t_next = Track(title="Play Next", url="https://example.com/next", duration=100)

        await player.enqueue(t1)
        await player.enqueue(t2)
        await player.enqueue_next(t_next)

        # First popped should be t_next
        popped = await player.dequeue()
        assert popped == t_next
        assert [t.title for t in player.queue] == ["Normal 1", "Normal 2"]


class TestPlayNextEmbed:
    """Tests for track_added_embed when is_next=True."""

    def test_track_added_embed_is_next_english(self):
        track = Track(title="Next Up Track", url="https://example.com/next", duration=210)
        embed = track_added_embed(track, position=1, eta_secs=210, locale="en", is_next=True)
        assert "play next" in embed.title.lower()
        assert "Next Up Track" in embed.description
        assert any(f.value == "#1" and "Position" in f.name for f in embed.fields)

    def test_track_added_embed_is_next_thai(self):
        track = Track(title="เพลงถัดไป", url="https://example.com/next_th", duration=180)
        embed = track_added_embed(track, position=1, eta_secs=180, locale="th", is_next=True)
        assert "เพลงถัดไป" in embed.title
        assert "เพลงถัดไป" in embed.description
        assert any(f.value == "#1" for f in embed.fields)

    def test_track_added_embed_normal_vs_next(self):
        track = Track(title="Normal Track", url="https://example.com/normal", duration=120)
        embed_norm = track_added_embed(track, position=5, eta_secs=600, locale="en", is_next=False)
        assert "Added to Queue" in embed_norm.title
        assert any(f.value == "#5" and "Position" in f.name for f in embed_norm.fields)

        embed_next = track_added_embed(track, position=1, eta_secs=60, locale="en", is_next=True)
        assert "play next" in embed_next.title.lower()
        assert any(f.value == "#1" and "Position" in f.name for f in embed_next.fields)
