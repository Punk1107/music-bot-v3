# -*- coding: utf-8 -*-
"""
chapters/seek_handler.py — Seamless FFmpeg chapter-seek for Music Bot V3 Feature 1.2.

Problem: calling vc.stop() normally triggers after_play() → _play_next() → next track.
Solution: increment player._play_seq BEFORE stopping, so the stale after_play callback
          is recognised as outdated and does NOT advance the queue.
          We then re-create the FFmpeg source at the new timestamp and resume playback
          immediately on the same track, updating play_start_time for accurate progress.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Optional

import discord

import config
from core.audio import AudioEffectsProcessor

if TYPE_CHECKING:
    from main import MusicBot
    from chapters.detector import Chapter

logger = logging.getLogger(__name__)


async def seek_to_chapter(
    bot:         "MusicBot",
    guild_id:    int,
    chapter:     "Chapter",
    interaction: Optional[discord.Interaction] = None,
) -> bool:
    """
    Seamlessly seek the current audio stream to the start of `chapter`.

    Steps:
      1. Validate: voice client is connected and playing.
      2. Grab stream URL from player (either cached or re-resolve).
      3. Increment _play_seq to invalidate the pending after_play callback.
      4. Stop current FFmpeg source silently.
      5. Build new FFmpegPCMAudio with -ss seek offset.
      6. Start playback immediately.
      7. Update player.play_start_time so elapsed progress is accurate.

    Returns True on success, False on error.
    if hasattr(bot, "seek") and bot.seek:
        return await bot.seek.seek_to(guild_id, int(chapter.start_sec))

    guild  = bot.get_guild(guild_id)
    if not guild:
        return False

    vc: Optional[discord.VoiceClient] = guild.voice_client
    if not vc or not vc.is_connected():
        logger.warning("seek_to_chapter: no voice client for guild %d", guild_id)
        return False

    player = bot.get_player(guild_id)
    track  = player.now_playing

    if not track:
        logger.warning("seek_to_chapter: no now_playing for guild %d", guild_id)
        return False

    target_sec = chapter.start_sec

    # ── Resolve stream URL ────────────────────────────────────────────────────
    # Use cached stream URL if available, otherwise re-resolve (slower)
    stream_url: Optional[str] = None

    if track.stream_url_cache and track.stream_url_expires:
        import time
        if time.monotonic() < track.stream_url_expires - 60:
            stream_url = track.stream_url_cache

    if not stream_url:
        try:
            stream_url = await asyncio.wait_for(
                bot.youtube.get_stream_url(track.url, track),
                timeout=15.0,
            )
        except Exception as exc:
            logger.error("seek_to_chapter: stream URL resolve failed: %s", exc)
            return False

    if not stream_url:
        logger.error("seek_to_chapter: no stream URL available")
        return False

    # ── Build FFmpeg options with seek offset ─────────────────────────────────
    cfg_server = await bot.db.get_server_config(guild_id)

    audio_processor = AudioEffectsProcessor()
    ffmpeg_opts = audio_processor.build_ffmpeg_options(
        effects         = player.effects,
        volume          = player.volume,
        quality         = cfg_server.audio_quality,
        seek_seconds    = int(target_sec),
        speed           = player.playback_speed,
        pitch_semitones = player.pitch_semitones,
        crossfade_secs  = 0,        # no crossfade on manual seek
        silence_trim    = player.silence_trim,
        replay_gain     = player.replay_gain,
    )

    before_opts = ffmpeg_opts.get("before_options", "")
    after_opts  = ffmpeg_opts.get("options", "")

    # ── Perform the seek atomically ───────────────────────────────────────────
    # Step 1: Capture current seq and bump it so the NEXT after_play from the
    #         old source is treated as stale.
    old_seq = player._play_seq
    player._play_seq += 1
    captured_seq = player._play_seq

    # Step 2: Define new after_play that uses the bumped seq
    def after_seek(error: Optional[Exception]) -> None:
        if error:
            logger.error("Seek playback error in guild %d: %s", guild_id, error)
        current_player = bot.get_player(guild_id)
        if current_player._play_seq != captured_seq:
            logger.debug(
                "guild %d: after_seek is stale (seq %d != %d) — ignoring.",
                guild_id, captured_seq, current_player._play_seq,
            )
            return
        # Normal end of track → trigger _play_next via MusicCog
        music_cog = bot.cogs.get("Music")
        if music_cog:
            asyncio.run_coroutine_threadsafe(
                music_cog._play_next(guild_id), bot.loop
            )

    # Step 3: Stop current source (stale after_play will see old_seq != captured_seq)
    if vc.is_playing() or vc.is_paused():
        vc.stop()
        # Give discord.py a tick to process stop
        await asyncio.sleep(0.15)

    # Step 4: Build and start new source
    try:
        source = discord.FFmpegPCMAudio(
            stream_url,
            before_options = before_opts,
            options        = after_opts,
        )
        source = discord.PCMVolumeTransformer(source, volume=player.volume)
        vc.play(source, after=after_seek)
    except Exception as exc:
        logger.error("seek_to_chapter: FFmpeg start failed: %s", exc)
        # Restore seq so the original track can still end naturally
        player._play_seq = old_seq
        return False

    # Step 5: Update play_start_time so elapsed/progress is correct
    # Offset back by target_sec so that elapsed_seconds = utcnow - start_time + target_sec
    now = datetime.datetime.now(datetime.timezone.utc)
    player.play_start_time = now - datetime.timedelta(seconds=target_sec)

    # Invalidate cached embed progress step so the NP embed refreshes immediately
    player._np_last_bar_step = -1

    logger.info(
        "guild %d: seeked to chapter '%s' @ %ds for track '%s'",
        guild_id, chapter.title, int(target_sec), track.title[:50],
    )
    return True
