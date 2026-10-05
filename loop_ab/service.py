# -*- coding: utf-8 -*-
"""
loop_ab/service.py — Loop A-B segment repeat service for Music Bot V3 (Feature 2.7).
Manages audio segment repetition via FFmpeg seek and asynchronous scheduling timer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class LoopABService:
    """Service controlling A-B audio segment looping with timer-based repeat."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def start_loop(self, guild_id: int, start_sec: int, end_sec: int) -> Tuple[bool, str]:
        """
        Start looping audio between start_sec and end_sec.
        Returns (success: bool, error_message: str).
        """
        player = self.bot.get_player(guild_id)
        track = player.now_playing
        if not track:
            return False, "no_track"

        if start_sec < 0 or end_sec <= start_sec:
            return False, "invalid_times"

        dur = track.duration
        if dur > 0 and end_sec > dur:
            end_sec = dur

        # Cancel any active A-B loop on this player
        player.cancel_loop_ab()

        # Set range
        player.loop_ab_range = (start_sec, end_sec)

        # Seek immediately to the start position
        ok = False
        if hasattr(self.bot, "seek") and self.bot.seek:
            ok = await self.bot.seek.seek_to(guild_id, start_sec)
        else:
            logger.warning("guild %d: LoopABService requires SeekService", guild_id)

        if not ok:
            player.loop_ab_range = None
            return False, "seek_failed"

        # Spawn loop timer task
        # Pass the freshly updated play_seq from the seek
        task = asyncio.create_task(
            self._loop_worker(guild_id, start_sec, end_sec, player._play_seq),
            name=f"loop_ab_{guild_id}",
        )
        if hasattr(self.bot, "track_task"):
            self.bot.track_task(task)
        player.loop_ab_task = task
        logger.info("guild %d: Loop A-B started from %ds to %ds", guild_id, start_sec, end_sec)
        return True, ""

    def stop_loop(self, guild_id: int) -> bool:
        """Stop active Loop A-B. Returns True if a loop was cancelled, False if none active."""
        player = self.bot.get_player(guild_id)
        if not player.loop_ab_range and not player.loop_ab_task:
            return False

        player.cancel_loop_ab()
        logger.info("guild %d: Loop A-B stopped", guild_id)
        return True

    def get_status(self, guild_id: int) -> Optional[Tuple[int, int]]:
        """Return (start_sec, end_sec) if active, else None."""
        player = self.bot.get_player(guild_id)
        return player.loop_ab_range

    async def _loop_worker(
        self,
        guild_id: int,
        start_sec: int,
        end_sec: int,
        captured_seq: int,
    ) -> None:
        """Background worker that continuously restarts playback at start_sec when end_sec is reached."""
        try:
            while True:
                player = self.bot.get_player(guild_id)
                # Verify loop is still configured for this range
                if player.loop_ab_range != (start_sec, end_sec):
                    break

                # Account for speed when calculating sleep duration
                segment_duration = max(0.5, float(end_sec - start_sec))
                speed = player.playback_speed if player.playback_speed > 0 else 1.0
                target_playback_secs = segment_duration / speed

                # Count down only while actually playing (hold while paused)
                elapsed_played = 0.0
                aborted = False
                while elapsed_played < target_playback_secs:
                    guild = self.bot.get_guild(guild_id)
                    vc = guild.voice_client if guild else None
                    if not vc or not vc.is_connected() or not (vc.is_playing() or vc.is_paused()):
                        aborted = True
                        break

                    player = self.bot.get_player(guild_id)
                    if player.loop_ab_range != (start_sec, end_sec) or player._play_seq != captured_seq:
                        aborted = True
                        break

                    if vc.is_playing():
                        tick = min(0.25, target_playback_secs - elapsed_played)
                        await asyncio.sleep(tick)
                        elapsed_played += tick
                    else:
                        await asyncio.sleep(0.25)

                if aborted:
                    break

                guild = self.bot.get_guild(guild_id)
                vc = guild.voice_client if guild else None
                if not vc or not vc.is_connected() or not (vc.is_playing() or vc.is_paused()):
                    break

                # Execute seek back to start_sec
                if hasattr(self.bot, "seek") and self.bot.seek:
                    success = await self.bot.seek.seek_to(guild_id, start_sec)
                    if not success:
                        logger.warning("guild %d: Loop A-B re-seek to %ds failed", guild_id, start_sec)
                        break
                    # seek_to increments _play_seq, update our captured_seq for the next iteration
                    captured_seq = player._play_seq
                else:
                    break

        except asyncio.CancelledError:
            logger.debug("guild %d: Loop A-B worker task cancelled", guild_id)
        except Exception as exc:
            logger.error("guild %d: Loop A-B worker error: %s", guild_id, exc)
        finally:
            # Clean up if loop stopped unexpectedly
            p = self.bot.get_player(guild_id)
            if p.loop_ab_range == (start_sec, end_sec):
                p.loop_ab_range = None
