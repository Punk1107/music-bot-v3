# -*- coding: utf-8 -*-
"""
core/player.py — Per-guild music player state for Music Bot V3.

V3 Changes:
  - auto_playlist_mode: flag for auto-fill from history when queue empties
  - favorites_cache: lightweight set of user_id → set of track URLs for quick lookup
  - text_channel stored as Optional[discord.TextChannel] for messages
  - idle_since tracked for auto-disconnect logic

Tier-S additions:
  - skip_votes: in-memory set of user_ids that voted to skip (Feature 1)
  - jump_to: skip directly to a queue position (Feature 8)
  - eta_seconds: ETA before a track at a given position plays (Feature 9)

Tier A/A+/B additions:
  - sleep_timer_task / sleep_timer_end: F16 Sleep Timer
  - alone_since / alone_leave_task: F17 Auto Leave
  - auto_paused: F18 Auto Pause
  - undo_stack: F19 Queue Undo
  - _transaction: F20 Queue Transaction
  - playback_speed / pitch_semitones / crossfade_seconds: F21/F22/F23
  - silence_trim / replay_gain: F24/F25
  - embed_theme: F27 Theme System
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from models.enums import AudioEffect, LoopMode
from models.track import Track

if TYPE_CHECKING:
    import discord


# ── Undo Entry (F19) ────────────────────────────────────────────────────

@dataclass
class UndoEntry:
    operation: str           # 'remove', 'clear', 'shuffle', 'move'
    snapshot:  list[Track]   # deep-copy of queue before the op
    extra:     Any = None    # optional extra info (e.g. removed track, position)


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

        # ── Playback concurrency guard (Bug 1 + 3) ────────────────────────────
        # Prevents two coroutines from entering _play_next simultaneously.
        # A new Lock is created on reset() so it is never in a locked state
        # after a full stop.
        self._playing_lock: asyncio.Lock = asyncio.Lock()

        # ── Playback sequence counter (Bug 3) ─────────────────────────────────
        # Incremented on finish_track() and reset(). The after_play FFmpeg
        # callback captures the seq at creation time; if it has changed by the
        # time the callback fires, the callback is stale (skip/stop raced) and
        # must not trigger _play_next.
        self._play_seq: int = 0

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

        # ── Intentional disconnect flag ───────────────────────────────────────
        # Set to True before calling vc.disconnect() from /stop or /leave so
        # that _try_reconnect knows NOT to attempt a reconnect.
        self.intentional_disconnect: bool = False

        # ── V3: Auto-playlist ─────────────────────────────────────────────────
        self.auto_playlist_mode: bool = False

        # ── V3: Prefetch task reference ───────────────────────────────────────
        self._prefetch_task: Optional[asyncio.Task] = None

        # ── History (last played, for loop:track) ─────────────────────────────
        self._history_track: Optional[Track] = None

        # ── Tier-S: Vote Skip (Feature 1) ─────────────────────────────────
        # Set of user_ids that have voted to skip the current track.
        # Reset automatically when the track finishes or is skipped.
        self.skip_votes: set[int] = set()

        # ── Tier A: Sleep Timer (F16) ───────────────────────────────────
        self.sleep_timer_task: Optional[asyncio.Task] = None
        self.sleep_timer_end:  Optional[datetime]     = None

        # ── Tier A: Auto Leave Alone (F17) ──────────────────────────────
        self.alone_since:      Optional[datetime]     = None
        self.alone_leave_task: Optional[asyncio.Task] = None

        # ── Tier A: Auto Pause (F18) ──────────────────────────────────
        self.auto_paused: bool = False  # True when bot paused due to empty channel

        # ── Tier A: Queue Undo Stack (F19) ────────────────────────────
        self.undo_stack: deque[UndoEntry] = deque(maxlen=5)

        # ── Tier A: Queue Transaction (F20) ───────────────────────────
        self._transaction: Optional[list[Track]] = None  # snapshot for rollback

        # ── Tier A+: Playback Controls (F21–F25) ───────────────────────
        self.playback_speed:    float = 1.0   # F21: 0.5–2.0
        self.pitch_semitones:   int   = 0     # F22: -6 to +6
        self.crossfade_seconds: int   = 0     # F23: 0 (off), 3, 5, 8
        self.silence_trim:      bool  = False # F24
        self.replay_gain:       bool  = False # F25

        # ── Tier B: Embed Theme (F27) ─────────────────────────────────
        self.embed_theme: str = "classic"  # matches EmbedTheme values

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
        # Advance sequence so stale after_play callbacks are rejected (Bug 3)
        self._play_seq += 1
        # Reset vote-skip state for the next track
        self.skip_votes.clear()

    # ── Tier A: Undo (F19) ─────────────────────────────────────────

    def undo_push(self, operation: str, extra: Any = None) -> None:
        """Snapshot the current queue and push to the undo stack."""
        self.undo_stack.append(
            UndoEntry(operation=operation, snapshot=list(self._queue), extra=extra)
        )

    def undo_pop(self) -> Optional[UndoEntry]:
        """Pop the most recent undo entry (or None if stack is empty)."""
        return self.undo_stack.pop() if self.undo_stack else None

    async def apply_undo(self, entry: UndoEntry) -> None:
        """Restore the queue from an UndoEntry snapshot."""
        async with self.queue_lock:
            self._queue = deque(entry.snapshot)

    # ── Tier A: Transaction (F20) ─────────────────────────────────────

    def begin_transaction(self) -> bool:
        """Snapshot queue to transaction buffer. Returns False if already open."""
        if self._transaction is not None:
            return False
        self._transaction = list(self._queue)
        return True

    def commit_transaction(self) -> bool:
        """Finalise transaction (discard snapshot). Returns False if none open."""
        if self._transaction is None:
            return False
        self._transaction = None
        return True

    async def rollback_transaction(self) -> bool:
        """Restore queue from transaction snapshot. Returns False if none open."""
        if self._transaction is None:
            return False
        async with self.queue_lock:
            self._queue = deque(self._transaction)
        self._transaction = None
        return True

    # ── Tier A: Sleep Timer helpers (F16) ──────────────────────────────

    def cancel_sleep_timer(self) -> bool:
        """Cancel any running sleep timer. Returns True if one was cancelled."""
        if self.sleep_timer_task and not self.sleep_timer_task.done():
            self.sleep_timer_task.cancel()
            self.sleep_timer_task = None
            self.sleep_timer_end  = None
            return True
        self.sleep_timer_task = None
        self.sleep_timer_end  = None
        return False

    def sleep_remaining_seconds(self) -> int:
        """Seconds remaining until sleep timer fires (0 if not active)."""
        if not self.sleep_timer_end:
            return 0
        from datetime import datetime, timezone
        delta = (self.sleep_timer_end - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))

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

    # ── Tier-S: ETA (Feature 9) ───────────────────────────────────────────────

    def eta_seconds(self, position: int) -> int:
        """
        Calculate estimated seconds until the track at `position` (1-based)
        in the queue starts playing.

        position=1  → next up after current track finishes
        position=N  → after (N-1) tracks before it complete
        """
        eta = self.remaining_seconds
        queue = self.queue
        for i, track in enumerate(queue):
            if i + 1 >= position:  # reached or passed target position
                break
            eta += (track.duration or 0)
        return max(0, eta)

    def skip_vote_threshold(self, voice_member_count: int) -> int:
        """Minimum votes needed to skip (ceil of 50% of voice members)."""
        import math
        return max(1, math.ceil(voice_member_count * 0.5))

    # ── Tier-S: Queue Jump (Feature 8) ───────────────────────────────────────

    async def jump_to(self, index: int) -> Optional[Track]:
        """
        Remove all tracks before `index` (0-based) so the track at that
        index becomes the next to play. Returns the target Track or None
        if out of range.

        Caller is responsible for stopping the current playback to trigger
        _play_next(), which will dequeue the now-first track.
        """
        async with self.queue_lock:
            lst = list(self._queue)
            if not 0 <= index < len(lst):
                return None
            target = lst[index]
            # Drop everything before the target
            self._queue = deque(lst[index:])
            return target

    # ── Prefetch ──────────────────────────────────────────────────────────────

    def cancel_prefetch(self) -> None:
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
            self._prefetch_task = None

    def adaptive_prefetch_limit(self) -> int:
        """
        F33: Adaptive Prefetch — return how many upcoming tracks to pre-warm.

        Strategy:
          Queue ≥ 20 tracks  → 8  (long queue: get far ahead)
          Queue ≥ 10 tracks  → 6
          Queue ≥  5 tracks  → 4
          Queue ≥  2 tracks  → 3
          Queue  < 2 tracks  → 2  (minimum: always pre-warm at least next track)
        """
        q = len(self._queue)
        if q >= 20:
            return 8
        if q >= 10:
            return 6
        if q >= 5:
            return 4
        if q >= 2:
            return 3
        return 2

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Full reset — called on stop or total disconnect."""
        self.cancel_prefetch()
        self.cancel_sleep_timer()

        # Cancel auto-leave task
        if self.alone_leave_task and not self.alone_leave_task.done():
            self.alone_leave_task.cancel()
        self.alone_leave_task = None
        self.alone_since       = None

        self._queue.clear()
        self.now_playing           = None
        self.play_start_time       = None
        self.now_playing_msg       = None
        self.now_playing_msg_id    = None
        self._history_track        = None
        self.loop_mode             = LoopMode.OFF
        self.effects               = []
        self.volume                = 1.0
        self.idle_since            = datetime.now(timezone.utc)
        self.auto_playlist_mode    = False
        self.skip_votes            = set()
        self.auto_paused           = False
        self.undo_stack.clear()
        self._transaction          = None
        # A+: reset audio controls to defaults
        self.playback_speed        = 1.0
        self.pitch_semitones       = 0
        self.crossfade_seconds     = 0
        self.silence_trim          = False
        self.replay_gain           = False
        # Bug 1+3: advance play_seq so stale after_play callbacks no-op,
        # and replace the playing lock (may be locked from a concurrent call).
        self._play_seq    += 1
        self._playing_lock = asyncio.Lock()
        # Note: intentional_disconnect is NOT reset here on purpose.
        # It is set True after reset() in /stop and /leave, then cleared
        # in _ensure_voice() when a new voice connection is established.
