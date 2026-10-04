# -*- coding: utf-8 -*-
"""
seek/service.py — Seek & Seamless Hot-Reload Service for Music Bot V3.

Provides atomic seek, fast-forward, rewind, replay, and hot-reload capabilities
without triggering stale after_play() callbacks or skipping tracks in queue.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING, Optional, Tuple

import discord

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class SeekService:
    """Service handling audio seeking and hot-reloading for guild voice clients."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def seek_to(self, guild_id: int, target_seconds: int) -> bool:
        """
        Seamlessly seek the current audio playback to target_seconds.

        Returns True if seek succeeded, False otherwise.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning("seek_to: guild %d not found", guild_id)
            return False

        vc: Optional[discord.VoiceClient] = guild.voice_client
        if not vc or not vc.is_connected():
            logger.warning("seek_to: no connected voice client for guild %d", guild_id)
            return False

        player = self.bot.get_player(guild_id)
        track = player.now_playing
        if not track:
            logger.warning("seek_to: no active track playing for guild %d", guild_id)
            return False

        # Clamp target seconds
        dur = track.duration
        if dur > 0:
            target_sec = max(0, min(int(target_seconds), dur - 1))
        else:
            target_sec = max(0, int(target_seconds))

        # ── Resolve stream URL ────────────────────────────────────────────────
        stream_url: Optional[str] = None
        if track.stream_url_cache and track.stream_url_expires:
            if time.monotonic() < track.stream_url_expires - 60:
                stream_url = track.stream_url_cache

        if not stream_url:
            try:
                stream_url = await asyncio.wait_for(
                    self.bot.youtube.get_stream_url(track.url, track),
                    timeout=15.0,
                )
            except Exception as exc:
                logger.error("seek_to: failed to resolve stream URL for guild %d: %s", guild_id, exc)
                return False

        if not stream_url:
            logger.error("seek_to: empty stream URL for guild %d", guild_id)
            return False

        # ── Build FFmpeg options with seek offset ─────────────────────────────
        from equalizer.presets import build_equalizer_filter
        from pan.filter import build_pan_filter, build_stereo_enhance_filter

        eq_filter = build_equalizer_filter(getattr(player, "equalizer_bands", None))
        pan_filter = build_pan_filter(getattr(player, "pan_balance", 0.0))
        stereo_filter = build_stereo_enhance_filter(getattr(player, "stereo_width", 1.0))
        loudnorm_flag = getattr(player, "loudnorm", False)

        cfg_server = await self.bot.db.get_server_config(guild_id)
        ffmpeg_opts = self.bot.audio_processor.build_ffmpeg_options(
            effects=player.effects,
            volume=1.0,  # Unity gain; live volume is handled by PCMVolumeTransformer
            quality=cfg_server.audio_quality,
            seek_seconds=target_sec,
            speed=player.playback_speed,
            pitch_semitones=player.pitch_semitones,
            crossfade_secs=0,  # No crossfade on seek/hot-reload
            silence_trim=player.silence_trim,
            replay_gain=player.replay_gain,
            equalizer_filter=eq_filter,
            loudnorm=loudnorm_flag,
            pan_filter=pan_filter,
            stereo_filter=stereo_filter,
        )

        before_opts = ffmpeg_opts.get("before_options", "")
        after_opts = ffmpeg_opts.get("options", "")

        # ── Atomic sequence invalidation ──────────────────────────────────────
        old_seq = player._play_seq
        player._play_seq += 1
        captured_seq = player._play_seq

        def after_seek(error: Optional[Exception]) -> None:
            if error:
                logger.error("guild %d seek playback error: %s", guild_id, error)
            current_player = self.bot.get_player(guild_id)
            if current_player._play_seq != captured_seq:
                logger.debug(
                    "guild %d: after_seek stale (seq %d != %d), ignoring.",
                    guild_id, captured_seq, current_player._play_seq,
                )
                return

            music_cog = self.bot.cogs.get("Music")
            if music_cog:
                asyncio.run_coroutine_threadsafe(
                    music_cog._play_next(guild_id), self.bot.loop
                )

        # Stop existing audio playback silently
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            for _ in range(15):
                if not (vc.is_playing() or vc.is_paused()):
                    break
                await asyncio.sleep(0.02)

        # Start new source with seek offset
        try:
            source = discord.FFmpegPCMAudio(
                stream_url,
                before_options=before_opts,
                options=after_opts,
            )
            source = discord.PCMVolumeTransformer(source, volume=player.volume)
            vc.play(source, after=after_seek)
        except Exception as exc:
            logger.error("guild %d: FFmpeg start failed on seek: %s", guild_id, exc)
            player._play_seq = old_seq
            return False

        # Update player elapsed timing
        now = datetime.datetime.now(datetime.timezone.utc)
        player.play_start_time = now - datetime.timedelta(seconds=target_sec)
        player._np_last_bar_step = -1

        logger.info(
            "guild %d: successfully seeked to %ds on track '%s'",
            guild_id, target_sec, track.title[:50],
        )
        return True

    async def forward(self, guild_id: int, seconds: int = 15) -> Tuple[bool, int]:
        """
        Fast-forward current track by specified seconds.
        Returns (success: bool, new_target_seconds: int).
        """
        player = self.bot.get_player(guild_id)
        if not player.now_playing:
            return False, 0

        dur = player.now_playing.duration
        max_target = max(0, dur - 1) if dur > 0 else player.elapsed_seconds + seconds
        target = min(max_target, player.elapsed_seconds + seconds)
        ok = await self.seek_to(guild_id, target)
        return ok, target

    async def rewind(self, guild_id: int, seconds: int = 15) -> Tuple[bool, int]:
        """
        Rewind current track by specified seconds.
        Returns (success: bool, new_target_seconds: int).
        """
        player = self.bot.get_player(guild_id)
        if not player.now_playing:
            return False, 0

        target = max(0, player.elapsed_seconds - seconds)
        ok = await self.seek_to(guild_id, target)
        return ok, target

    async def replay(self, guild_id: int) -> bool:
        """Replay current track from 0:00."""
        return await self.seek_to(guild_id, 0)

    async def hot_reload(self, guild_id: int) -> bool:
        """
        Hot-reload current audio stream at current elapsed position
        to apply new volume, effects, speed, pitch, etc. without advancing the queue.
        """
        player = self.bot.get_player(guild_id)
        if not player.now_playing:
            return False

        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return False

        return await self.seek_to(guild_id, player.elapsed_seconds)
