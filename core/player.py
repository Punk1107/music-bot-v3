# -*- coding: utf-8 -*-
"""
core/player.py — Per-guild music player state for Music Bot V3.

V3 Changes:
  - auto_playlist_mode: flag for auto-fill from history when queue empties
  - favorites_cache: lightweight set of user_id → set of track URLs for quick lookup
  - text_channel stored as Optional[discord.TextChannel] for messages
  - idle_since tracked for auto-disconnect logic
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from models.enums import AudioEffect, LoopMode
from models.track import Track

if TYPE_CHECKING:
    import discord


class GuildPlayer:
    """
    Mutable state container for a single guild's music session.

    All queue-mutating methods acquire self.queue_lock — safe for concurrent
    Discord interaction events fired on the asyncio event loop.
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id: int = guild_id

        # ── Queue ──────────────────────────────────────────────────────────────
        self._queue: deque[Track] = deque()
        self.queue_lock: asyncio.Lock = asyncio.Lock()

        # ── Now-playing ───────────────────────────────────────────────────────
        self.now_playing:        Optional[Track]    = None
        self.play_start_time:    Optional[datetime] = None
        self.now_playing_msg:    Optional[object]   = None  # discord.Message
        self.now_playing_msg_id: Optional[int]      = None  # fallback ID

        # ── Controls ──────────────────────────────────────────────────────────
        self.loop_mode:  LoopMode          = LoopMode.OFF
        self.effects:    list[AudioEffect] = []
        self.volume:     float             = 1.0     # 0.0 – 2.0

        # ── Self-healing ──────────────────────────────────────────────────────
        self.last_channel_id: Optional[int]          = None
        self.text_channel:    Optional[object]        = None  # discord.TextChannel

        # ── Idle tracking ─────────────────────────────────────────────────────
        self.idle_since: Optional[datetime] = None

        # ── V3: Auto-playlist ─────────────────────────────────────────────────
        self.auto_playlist_mode: bool = False

        # ── V3: Prefetch task reference ───────────────────────────────────────
        self._prefetch_task: Optional[asyncio.Task] = None

        # ── History (last played, for loop:track) ─────────────────────────────
        self._history_track: Optional[Track] = None

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._queue)

    @property
    def queue(self) -> list[Track]:
        """Snapshot of the queue as a list (safe read without lock)."""
        return list(self._queue)

    async def enqueue(self, track: Track) -> int:
        """Add one track and return new queue length."""
        async with self.queue_lock:
            self._queue.append(track)
            return len(self._queue)

    async def extend(self, tracks: list[Track]) -> int:
        """Bulk-add tracks. Returns new queue length."""
        async with self.queue_lock:
            self._queue.extend(tracks)
            return len(self._queue)

    async def dequeue(self) -> Optional[Track]:
        """
        Pop the next track respecting loop mode.

        - OFF / QUEUE: pop left (next in line)
        - TRACK: return the current track again (no pop)
        - QUEUE with empty deque: no-op → returns None
        """
        async with self.queue_lock:
            if self.loop_mode == LoopMode.TRACK and self._history_track:
                return self._history_track

            if not self._queue:
                return None

            track = self._queue.popleft()

            if self.loop_mode == LoopMode.QUEUE:
                self._queue.append(track)

            return track

    async def remove(self, index: int) -> Optional[Track]:
        """Remove track at 0-based index. Returns removed track or None."""
        async with self.queue_lock:
            lst = list(self._queue)
            if not 0 <= index < len(lst):
                return None
            removed = lst.pop(index)
            self._queue = deque(lst)
            return removed

    async def move(self, from_idx: int, to_idx: int) -> bool:
        """Atomically move a track from one position to another. Returns success."""
        async with self.queue_lock:
            lst = list(self._queue)
            n = len(lst)
            if not (0 <= from_idx < n and 0 <= to_idx < n):
                return False
            track = lst.pop(from_idx)
            lst.insert(to_idx, track)
            self._queue = deque(lst)
            return True

    async def shuffle(self) -> None:
        async with self.queue_lock:
            lst = list(self._queue)
            random.shuffle(lst)
            self._queue = deque(lst)

    async def clear(self) -> int:
        """Clear queue and return how many tracks were removed."""
        async with self.queue_lock:
            count = len(self._queue)
            self._queue.clear()
            return count

    async def finish_track(self) -> None:
        """Mark current track finished (stores to history for LOOP:TRACK)."""
        if self.now_playing:
            self._history_track = self.now_playing

    # ── Progress ──────────────────────────────────────────────────────────────

    @property
    def elapsed_seconds(self) -> int:
        if not self.play_start_time or not self.now_playing:
            return 0
        delta = datetime.now(timezone.utc) - self.play_start_time
        return min(int(delta.total_seconds()), self.now_playing.duration)

    @property
    def remaining_seconds(self) -> int:
        if not self.now_playing:
            return 0
        return max(0, self.now_playing.duration - self.elapsed_seconds)

    def progress_fraction(self) -> float:
        """0.0 – 1.0 progress through the current track."""
        if not self.now_playing or not self.now_playing.duration:
            return 0.0
        return min(1.0, self.elapsed_seconds / self.now_playing.duration)

    # ── Prefetch ──────────────────────────────────────────────────────────────

    def cancel_prefetch(self) -> None:
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
            self._prefetch_task = None

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Full reset — called on stop or total disconnect."""
        self.cancel_prefetch()
        self._queue.clear()
        self.now_playing        = None
        self.play_start_time    = None
        self.now_playing_msg    = None
        self.now_playing_msg_id = None
        self._history_track     = None
        self.loop_mode          = LoopMode.OFF
        self.effects            = []
        self.volume             = 1.0
        self.idle_since         = datetime.now(timezone.utc)
        self.auto_playlist_mode = False
