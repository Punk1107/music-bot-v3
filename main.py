# -*- coding: utf-8 -*-
"""
main.py — Music Bot V3 entry point.

MusicBot class:
  - Initialises all core services (DB, YouTube, Spotify, audio, NLU, webserver)
  - Loads all cogs (music, queue, effects, info, favorites, admin)
  - Manages per-guild GuildPlayer registry
  - Background tasks:
      • idle_check    (every 30s)   — auto-disconnect idle guilds
      • queue_save    (every 5min)  — persist all queues to DB
      • np_refresh    (every 7s)    — update now-playing progress bar
      • cache_prune   (every 30min) — evict expired yt-dlp cache entries
      • analytics_prune (daily)     — prune old analytics rows
  - Event handlers:
      • on_ready           — log and sync commands
      • on_guild_join/remove
      • on_voice_state_update — idle detection
      • on_message         — request channel handler with NLU
  - Error handling: global on_command_error + on_interaction_error
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks
import aiohttp

import config
from config import setup_logging
from core.database       import DatabaseManager
from core.youtube        import YouTubeExtractor
from core.spotify        import SpotifyExtractor
from core.audio          import AudioEffectsProcessor
from core.audio_backend  import create_backend
from core.circuit_breaker import CircuitBreaker
from core.nlu            import NLUPipeline
from core.player         import GuildPlayer
from core.ffmpeg_pool    import FFmpegWarmPool       # Tier-S+ F13
from core.media_cache    import (                    # Tier-S+ F14 F15
    prewarm_queue_thumbnails, bg_refresh_metadata, prune_caches as prune_media_caches,
    cache_stats as media_cache_stats,
)
from core.lru_cache      import check_memory_pressure, combined_stats as lru_stats  # F31 F32
from core.self_test      import run_self_test, SelfTestReport                        # F34
from core.stability      import (                    # Stability suite
    ExceptionKind, classify_exception, log_with_kind,
    DeadTaskWatchdog, MemoryLeakDetector,
)
from core import metrics as metrics_module           # Runtime metrics snapshots
from core.startup_validator import validate_pre_login  # Pre-login validation
from webserver           import WebServer


setup_logging()
logger = logging.getLogger(__name__)

# ── Cogs to load ─────────────────────────────────────────────────────────────

_COGS = [
    "cogs.music",
    "cogs.queue_cog",
    "cogs.effects",
    "cogs.info",
    "cogs.favorites",
    "cogs.admin",
    "cogs.bookmark_cog",     # Tier-S  Feature 5: Queue Bookmarks
    "cogs.sleep_timer_cog",  # Tier A  Feature 16: Sleep Timer
    "cogs.playback_cog",     # Tier A+ Features 21-25: Speed/Pitch/Crossfade/Trim/Gain
    "cogs.presets_cog",      # Tier B  Feature 26: Guild Presets
    "cogs.theme_cog",        # Tier B  Feature 27: Theme System
    "cogs.language_cog",     # Perf    Feature 30: Localization (/language)
    "cogs.health_cog",       # Perf    Feature 35: Health Report (/health)
    "cogs.analytics_cog",    # Tier    Analytics Dashboard (/analytics)
]


class MusicBot(commands.Bot):
    """
    Main bot class for Music Bot V3.

    Services are initialised in setup_hook() so they run on the event loop.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states    = True
        intents.guilds          = True

        super().__init__(
            command_prefix  = commands.when_mentioned,
            intents         = intents,
            help_command    = None,
            application_id  = config.APP_ID,
        )

        # ── Service singletons ────────────────────────────────────────────────
        self.db:             DatabaseManager     = DatabaseManager()
        self.youtube:        YouTubeExtractor    = YouTubeExtractor()
        self.spotify:        SpotifyExtractor    = SpotifyExtractor()
        self.audio_processor: AudioEffectsProcessor = AudioEffectsProcessor()
        self.audio_backend                       = create_backend()
        self.nlu:            NLUPipeline         = NLUPipeline()
        self.webserver:      WebServer           = WebServer(self)
        self.ffmpeg_pool:    FFmpegWarmPool      = FFmpegWarmPool()  # Tier-S+ F13

        # ── Circuit breakers ──────────────────────────────────────────────────
        self.yt_breaker = CircuitBreaker(
            "youtube",
            failure_threshold = config.CIRCUIT_BREAKER_THRESHOLD,
            recovery_window   = config.CIRCUIT_BREAKER_WINDOW,
        )
        self.sp_breaker = CircuitBreaker(
            "spotify",
            failure_threshold = config.CIRCUIT_BREAKER_THRESHOLD,
            recovery_window   = config.CIRCUIT_BREAKER_WINDOW,
        )

        # ── Per-guild player registry ──────────────────────────────────────────────────
        self._players:   dict[int, GuildPlayer] = {}

        # ── Shared HTTP session (created in setup_hook) ──────────────────────────────
        self.http_session: Optional[aiohttp.ClientSession] = None

        # ── Timing ──────────────────────────────────────────────────────────────────
        self.start_time: datetime = datetime.now(timezone.utc)

        # ── Tier Performance: Self-test report (F34) ──────────────────────────
        self.self_test_report: Optional[SelfTestReport] = None

        # ── Stability subsystems (populated in setup_hook) ────────────────────
        self.task_watchdog:     Optional[DeadTaskWatchdog]             = None
        self.mem_leak_detector: Optional[MemoryLeakDetector]           = None
        self.metrics:           Optional[metrics_module.MetricsCollector] = None

    # ── Player registry ───────────────────────────────────────────────────────

    def get_player(self, guild_id: int) -> GuildPlayer:
        """Return (or create) the GuildPlayer for a guild."""
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id)
        return self._players[guild_id]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called once before the bot connects — ideal for async init."""
        logger.info("🚀 Music Bot V3 initialising…")

        # Initialise stability subsystems
        self.metrics           = metrics_module.MetricsCollector(self)
        self.mem_leak_detector = MemoryLeakDetector()
        self.task_watchdog     = DeadTaskWatchdog(interval=60.0)
        metrics_module.set_collector(self.metrics)

        # Shared aiohttp session
        self.http_session = aiohttp.ClientSession(
            connector    = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
            timeout      = aiohttp.ClientTimeout(total=30),
        )

        # Database
        await self.db.initialise()

        # Webserver
        await self.webserver.start()

        # Load cogs
        for cog in _COGS:
            try:
                await self.load_extension(cog)
                logger.info("✅ Loaded cog: %s", cog)
            except Exception as exc:
                logger.error("❌ Failed to load cog %s: %s", cog, exc)
                traceback.print_exc()

        # Sync commands if requested
        if config.SYNC_COMMANDS:
            logger.info("Syncing application commands globally…")
            try:
                synced = await self.tree.sync()
                logger.info("✅ Synced %d command(s)", len(synced))
            except Exception as exc:
                logger.error("Command sync failed: %s", exc)

        # Register all loops with the dead task watchdog
        if self.task_watchdog:
            for _loop_task, _loop_name in [
                (self._idle_check,        "idle_check"),
                (self._queue_save,        "queue_save"),
                (self._np_refresh,        "np_refresh"),
                (self._cache_prune,       "cache_prune"),
                (self._analytics_prune,   "analytics_prune"),
                (self._session_heartbeat, "session_heartbeat"),
                (self._memory_pressure,   "memory_pressure"),
                (self._metrics_snapshot,  "metrics_snapshot"),
                (self._memory_leak_detect,"memory_leak_detect"),
            ]:
                self.task_watchdog.register(_loop_task, _loop_name)

        # Start background tasks
        self._idle_check.start()
        self._queue_save.start()
        self._np_refresh.start()
        self._cache_prune.start()
        self._analytics_prune.start()
        self._session_heartbeat.start()  # Tier-S+ F11: session state heartbeat
        self._memory_pressure.start()    # Tier Performance F32: memory pressure handler
        self._metrics_snapshot.start()   # Stability: runtime metrics every 5 min
        self._memory_leak_detect.start() # Stability: memory leak detection every 30 min
        self._dead_task_watchdog.start() # Stability: dead task detector every 60 s

        # Tier-S+ F13: Warm FFmpeg pool
        asyncio.create_task(self.ffmpeg_pool.initialise())

        # Tier Performance F34: Startup self-test (runs concurrently, non-blocking)
        asyncio.create_task(self._run_startup_self_test())

        logger.info("✅ Setup complete. Bot is starting…")

    async def close(self) -> None:
        """Graceful shutdown: save queues, stop services, close connections."""
        logger.info("Shutting down Music Bot V3…")

        # Signal watchdog to stop restarting tasks
        if self.task_watchdog:
            self.task_watchdog.stop()

        # Cancel discord.ext.tasks loops
        _task_loops = [
            t for t in [
                self._idle_check, self._queue_save, self._np_refresh,
                self._cache_prune, self._analytics_prune,
                self._session_heartbeat, self._memory_pressure,
                self._metrics_snapshot, self._memory_leak_detect,
                self._dead_task_watchdog,
            ]
            if t is not None
        ]
        for t in _task_loops:
            t.cancel()
        await asyncio.gather(
            *[t.get_task() for t in _task_loops if t.get_task() is not None],
            return_exceptions=True,
        )

        # Cancel ALL floating asyncio tasks (prefetch, _leave_if_still_alone,
        # _delayed_resume, sleep timers, etc.) — every create_task() that was
        # launched but never explicitly cancelled.
        current_task  = asyncio.current_task()
        floating_tasks = [
            t for t in asyncio.all_tasks()
            if t is not current_task and not t.done()
        ]
        if floating_tasks:
            logger.info(
                "Cancelling %d floating asyncio task(s)…", len(floating_tasks)
            )
            for ft in floating_tasks:
                ft.cancel()
            await asyncio.gather(*floating_tasks, return_exceptions=True)

        # Tier-S+ F11: Persist FULL session state (including now_playing position)
        for guild_id, player in self._players.items():
            if player.now_playing or player.queue:
                guild = self.get_guild(guild_id)
                vc    = guild.voice_client if guild else None
                channel_id      = vc.channel.id if vc else (player.last_channel_id or 0)
                text_channel_id = player.text_channel.id if player.text_channel else 0
                try:
                    await self.db.save_session_state(
                        guild_id        = guild_id,
                        channel_id      = channel_id,
                        text_channel_id = text_channel_id,
                        now_playing     = player.now_playing,
                        elapsed_secs    = player.elapsed_seconds,
                        queue           = player.queue,
                        loop_mode       = player.loop_mode.value,
                        volume          = player.volume,
                        effects         = [e.value for e in player.effects],
                    )
                except Exception as exc:
                    logger.warning("Session state save on shutdown guild %d: %s", guild_id, exc)

        # Disconnect all voice clients with per-guild timeout
        for guild in self.guilds:
            vc = guild.voice_client
            if vc and vc.is_connected():
                try:
                    await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
                except Exception:
                    pass

        # Tier-S+ F13: Close FFmpeg warm pool
        await self.ffmpeg_pool.close()

        # Stop webserver (closes WebSocket connections gracefully)
        try:
            await asyncio.wait_for(self.webserver.stop(), timeout=10.0)
        except Exception as exc:
            logger.warning("Webserver stop error: %s", exc)

        # Close DB
        try:
            await asyncio.wait_for(self.db.close(), timeout=10.0)
        except Exception as exc:
            logger.warning("DB close error on shutdown: %s", exc)

        # Close HTTP session
        if self.http_session:
            try:
                await asyncio.wait_for(self.http_session.close(), timeout=5.0)
            except Exception:
                pass

        await super().close()
        logger.info("✅ Shutdown complete.")

    async def _run_startup_self_test(self) -> None:
        """Run self-test after bot is ready (F34)."""
        try:
            await self.wait_until_ready()
            self.self_test_report = await run_self_test(self)
        except Exception as exc:
            logger.error("Self-test runner error: %s", exc)

    # ── Events ────────────────────────────────────────────────────────────────

    async def on_ready(self) -> None:
        logger.info(
            "✅ Logged in as %s (ID: %d) | %d guilds",
            self.user, self.user.id, len(self.guilds)
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="music 🎵 | V3"
            )
        )

        # Auto-resume: restore queues saved from previous session
        if config.AUTO_RESUME:
            await self._restore_queues()

    async def _restore_queues(self) -> None:
        """
        Tier-S+ Feature 11: Full session state restore on bot start.
        Restores: queue, loop mode, volume, effects, and the now-playing track
        (which is re-added to front of queue so it plays first after resume).
        """
        from models.enums import LoopMode, AudioEffect
        for guild in self.guilds:
            try:
                # Try full session state first (Tier-S+)
                state = await self.db.load_session_state(guild.id)
                if state:
                    player = self.get_player(guild.id)
                    player.loop_mode = LoopMode(state["loop_mode"])
                    player.volume    = state["volume"]
                    player.effects   = [
                        AudioEffect(e) for e in state["effects"]
                        if e in [ae.value for ae in AudioEffect]
                    ]

                    # Restore text channel
                    if state["text_channel_id"]:
                        tc = guild.get_channel(state["text_channel_id"])
                        if tc:
                            player.text_channel = tc

                    # Rebuild queue: now_playing goes to front
                    restored_tracks = []
                    if state["now_playing"]:
                        # Put now_playing at front with elapsed position info attached
                        t = state["now_playing"]
                        t._resume_offset = state["elapsed_secs"]  # carry offset for Feature 12
                        restored_tracks.append(t)
                    restored_tracks.extend(state["queue"])

                    if restored_tracks:
                        await player.extend(restored_tracks)
                        logger.info(
                            "Full session restored for guild %d: %d tracks, loop=%s, vol=%.0f%%, effects=%s",
                            guild.id, len(restored_tracks),
                            player.loop_mode.value, player.volume * 100,
                            [e.value for e in player.effects],
                        )

                    # Clear state after successful restore (don't replay on next start)
                    await self.db.clear_session_state(guild.id)
                    continue

                # Fallback: legacy queue-only restore
                tracks = await self.db.load_queue(guild.id)
                if tracks:
                    player = self.get_player(guild.id)
                    await player.extend(tracks)
                    logger.info("Legacy queue restored for guild %d: %d tracks", guild.id, len(tracks))

            except Exception as exc:
                logger.warning("Session restore error for guild %d: %s", guild.id, exc)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Joined guild: %s (ID: %d)", guild.name, guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info("Left guild: %s (ID: %d)", guild.name, guild.id)
        player = self._players.pop(guild.id, None)
        if player:
            player.reset()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ) -> None:
        """Track idle state and handle bot's own voice disconnects (Feature 12).

        Also handles:
          F17: Auto-leave when bot is alone for 90+ seconds
          F18: Auto-pause when channel empties; resume on rejoin
        """
        guild  = member.guild
        player = self.get_player(guild.id)
        vc     = guild.voice_client

        # Feature 12: Detect when the BOT itself gets disconnected unexpectedly
        if member.id == self.user.id:
            was_connected = before.channel is not None
            now_connected = after.channel  is not None

            # Bug 2: Bot was MOVED to a different channel — update last_channel_id
            # so that future reconnect attempts target the correct channel.
            if before.channel and after.channel and before.channel != after.channel:
                player.last_channel_id = after.channel.id
                logger.debug(
                    "guild %d: Bot moved %s → %s — updated last_channel_id",
                    guild.id, before.channel.name, after.channel.name,
                )

            if was_connected and not now_connected:
                # Bot was kicked from voice / Discord dropped the connection
                if not player.intentional_disconnect and player.now_playing:
                    logger.info(
                        "guild %d: Bot voice disconnected unexpectedly — scheduling reconnect",
                        guild.id,
                    )
                    # Delegate reconnect + resume to MusicCog._try_reconnect
                    # by triggering _play_next after a short delay
                    music_cog = self.cogs.get("Music")
                    if music_cog:
                        async def _delayed_resume(gid: int) -> None:
                            await asyncio.sleep(2)
                            await music_cog._play_next(gid)
                        asyncio.create_task(_delayed_resume(guild.id))
            return

        # Ignore other bots
        if member.bot:
            return

        if not vc or not vc.channel:
            return

        non_bot_members = [m for m in vc.channel.members if not m.bot]
        human_count = len(non_bot_members)

        # ── F18: Auto Pause / Resume ──────────────────────────────────────────
        try:
            cfg = await self.db.get_server_config(guild.id)
        except Exception:
            cfg = None

        if cfg and cfg.auto_pause_empty:
            if human_count == 0 and vc.is_playing():
                vc.pause()
                player.auto_paused = True
                logger.debug("guild %d: Auto-paused (channel empty)", guild.id)
            elif human_count > 0 and player.auto_paused and vc.is_paused():
                vc.resume()
                player.auto_paused = False
                logger.debug("guild %d: Auto-resumed (member rejoined)", guild.id)

        # ── F17: Auto Leave When Alone ────────────────────────────────────────
        if cfg and cfg.auto_leave_alone:
            if human_count == 0:
                # Start the 90-second countdown if not already running
                if player.alone_since is None:
                    player.alone_since = datetime.now(timezone.utc)

                    async def _leave_if_still_alone(gid: int, _vc: discord.VoiceClient) -> None:
                        await asyncio.sleep(90)
                        _player = self.get_player(gid)
                        _guild  = self.get_guild(gid)
                        if not _guild:
                            return
                        _vc2 = _guild.voice_client
                        if not _vc2 or not _vc2.is_connected():
                            return
                        # Check still alone
                        still_alone = all(m.bot for m in _vc2.channel.members)
                        if still_alone:
                            logger.info("guild %d: Auto-leaving — alone for 90s", gid)
                            if _player.text_channel:
                                try:
                                    await _player.text_channel.send(
                                        "👋 ออกจากห้องเนื่องจากไม่มีคนอยู่ด้วย",
                                        delete_after=20,
                                    )
                                except Exception:
                                    pass
                            _player.reset()
                            _player.intentional_disconnect = True
                            try:
                                await _vc2.disconnect(force=True)
                            except Exception:
                                pass
                        _player.alone_since     = None
                        _player.alone_leave_task = None

                    player.alone_leave_task = asyncio.create_task(
                        _leave_if_still_alone(guild.id, vc)
                    )
            else:
                # Human rejoined — cancel the alone timer
                player.alone_since = None
                if player.alone_leave_task and not player.alone_leave_task.done():
                    player.alone_leave_task.cancel()
                    player.alone_leave_task = None
            return  # done with F17/F18 for non-bot member events

        # ── Legacy idle tracking (when F17 disabled) ──────────────────────────
        if human_count == 0:
            if player.idle_since is None:
                player.idle_since = datetime.now(timezone.utc)
        else:
            player.idle_since = None

    async def on_message(self, message: discord.Message) -> None:
        """
        Request channel handler (V3 NEW).

        If a message is sent in the configured request channel and is not from
        a bot, treat it as a /play invocation using the NLU + request channel logic.
        """
        if message.author.bot or not message.guild:
            await self.process_commands(message)
            return

        # Check if this channel is the request channel for the guild
        try:
            cfg = await self.db.get_server_config(message.guild.id)
        except Exception:
            await self.process_commands(message)
            return

        if cfg.request_channel_id and message.channel.id == cfg.request_channel_id:
            await self._handle_request_channel(message)
            return

        # NLU: if NLU enabled, try to parse music intents in any channel
        if config.NLU_ENABLED and message.content:
            result = self.nlu.parse(message.content)
            from models.enums import NLUIntent
            if result.intent != NLUIntent.UNKNOWN:
                # Let NLU handle recognised intents (only in non-request-channels)
                pass  # NLU is mainly for request channels in V3

        await self.process_commands(message)

    async def _handle_request_channel(self, message: discord.Message) -> None:
        """
        Process a message in the request channel.

        1. Try NLU intent classification
        2. If PLAY or unknown-but-looks-like-query → trigger /play
        3. If other intent → respond appropriately
        4. Delete user's message after processing
        """
        from models.enums import NLUIntent

        content = message.content.strip()
        if not content:
            return

        # Delete user message (optional — ignore errors)
        try:
            await message.delete()
        except Exception:
            pass

        result = self.nlu.parse(content)

        # Determine query to play
        query: Optional[str] = None

        if result.intent == NLUIntent.PLAY and result.query:
            query = result.query
        elif result.intent == NLUIntent.SKIP:
            guild = message.guild
            vc    = guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            try:
                reply = await message.channel.send("⏭ Skipped!", delete_after=5)
            except Exception:
                pass
            return
        elif result.intent == NLUIntent.PAUSE:
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
            try:
                await message.channel.send("⏸ Paused.", delete_after=5)
            except Exception:
                pass
            return
        elif result.intent == NLUIntent.RESUME:
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
            try:
                await message.channel.send("▶ Resumed.", delete_after=5)
            except Exception:
                pass
            return
        elif result.intent == NLUIntent.VOLUME and result.volume is not None:
            player = self.get_player(message.guild.id)
            player.volume = result.volume / 100
            try:
                await message.channel.send(f"🔊 Volume set to {result.volume}%", delete_after=5)
            except Exception:
                pass
            return
        elif result.intent == NLUIntent.UNKNOWN and self.nlu.is_music_query(content):
            query = content
        else:
            # Unknown and doesn't look like a query — ignore silently
            return

        if not query:
            return

        # Build a fake interaction context for play_track
        # We can't create a real Interaction, so we call the music backend directly
        music_cog = self.cogs.get("Music")
        if not music_cog:
            return

        # Check if user is in voice
        if not message.author.voice:
            try:
                await message.channel.send(
                    "❌ Join a voice channel first.", delete_after=8
                )
            except Exception:
                pass
            return

        # Get or join voice
        vc = message.guild.voice_client
        if not vc or not vc.is_connected():
            try:
                vc = await message.author.voice.channel.connect(timeout=10.0)
                player = self.get_player(message.guild.id)
                player.last_channel_id = message.author.voice.channel.id
                player.text_channel    = message.channel
            except Exception as exc:
                logger.warning("RC join failed: %s", exc)
                return

        # Resolve track
        from core.validator import validate_search_query
        from core.circuit_breaker import CircuitBreakerOpen

        is_safe, _ = validate_search_query(query)
        if not is_safe:
            return

        try:
            if self.youtube.is_youtube_url(query):
                track = await self.yt_breaker.call(self.youtube.get_track, query)
            else:
                results = await self.yt_breaker.call(self.youtube.search, query, 1)
                track   = results[0] if results else None
        except CircuitBreakerOpen:
            return
        except Exception as exc:
            logger.warning("RC play error: %s", exc)
            return

        if not track:
            try:
                await message.channel.send(f"❌ No results for `{query[:50]}`", delete_after=8)
            except Exception:
                pass
            return

        player = self.get_player(message.guild.id)
        track.requested_by_id   = message.author.id
        track.requested_by_name = message.author.display_name
        pos = await player.enqueue(track)

        asyncio.create_task(
            self.db.save_queue(message.guild.id, vc.channel.id, player.queue)
        )

        from utils.embeds import track_added_embed
        from utils.color_thief import get_dominant_color
        color = await get_dominant_color(track.thumbnail, self.http_session)
        try:
            await message.channel.send(
                embed=track_added_embed(track, pos, color, message.author),
                delete_after=15,
            )
        except Exception:
            pass

        if not vc.is_playing() and not vc.is_paused():
            await music_cog._play_next(message.guild.id)

    # ── Background Tasks ──────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def _idle_check(self) -> None:
        """
        Disconnect guilds idle longer than their configured timeout.

        Perf-1 Memory Audit: Also purges stale GuildPlayer entries for guilds
        the bot has left or that have no voice connection and nothing queued.
        Prevents _players from growing unboundedly across the bot's lifetime.
        """
        now = datetime.now(timezone.utc)
        stale_guilds: list[int] = []

        for guild_id, player in list(self._players.items()):
            guild = self.get_guild(guild_id)

            # Perf-1: guild no longer in bot's guild list (kicked / deleted)
            if not guild:
                stale_guilds.append(guild_id)
                continue

            vc = guild.voice_client

            # Perf-1: idle player with empty queue and no VC — free memory
            if (
                not player.now_playing
                and not player.queue
                and (not vc or not vc.is_connected())
            ):
                stale_guilds.append(guild_id)
                continue

            # Standard idle timeout check
            if player.now_playing or not player.idle_since:
                continue
            if not vc or not vc.is_connected():
                player.idle_since = None
                continue

            try:
                cfg = await self.db.get_server_config(guild_id)
                timeout = cfg.idle_timeout
            except Exception:
                timeout = config.IDLE_TIMEOUT

            elapsed = (now - player.idle_since).total_seconds()
            if elapsed >= timeout:
                logger.info("Auto-disconnecting guild %d (idle for %.0fs)", guild_id, elapsed)
                if player.text_channel:
                    try:
                        await player.text_channel.send(
                            "💤 Disconnected due to inactivity.\n*ออกจากห้องเนื่องจากไม่มีการใช้งาน*",
                            delete_after=30,
                        )
                    except Exception:
                        pass
                player.reset()
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                stale_guilds.append(guild_id)

        # Remove all stale players in one pass (after iterating)
        for gid in stale_guilds:
            p = self._players.pop(gid, None)
            if p:
                p.reset()
        if stale_guilds:
            logger.debug(
                "Pruned %d stale GuildPlayer entries from _players registry",
                len(stale_guilds),
            )

    @_idle_check.before_loop
    async def _before_idle_check(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=config.QUEUE_SAVE_INTERVAL)
    async def _queue_save(self) -> None:
        """Periodic queue persistence (write-ahead saves happen on enqueue too)."""
        for guild_id, player in self._players.items():
            q = player.queue
            if not q:
                continue
            guild = self.get_guild(guild_id)
            vc    = guild.voice_client if guild else None
            if not vc:
                continue
            try:
                await self.db.save_queue(guild_id, vc.channel.id, q)
            except Exception as exc:
                logger.debug("Periodic queue save error guild %d: %s", guild_id, exc)

    @_queue_save.before_loop
    async def _before_queue_save(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=7)
    async def _np_refresh(self) -> None:
        """
        Refresh now-playing embed progress bar every 7 seconds.

        Perf-1 optimisations:
          1. Use player._cached_base_color instead of re-fetching the thumbnail
             on every tick (thumbnail URL and dominant color don't change mid-track).
          2. Skip msg.edit() when the quantised progress hasn't advanced by at
             least one step (~1 / bar_width ≈ 3 %).  This prevents a burst of
             Discord API calls on very short tracks or when the bot is paused.
        """
        for guild_id, player in self._players.items():
            if not player.now_playing or not player.now_playing_msg:
                continue
            try:
                from utils.embeds import now_playing_embed
                from utils.color_thief import animated_embed_color

                # Perf-1: reuse the color cached when playback started
                base_color = getattr(player, "_cached_base_color", None)
                if base_color is None:
                    # Fallback: fetch (only happens if track started before this patch)
                    from utils.color_thief import get_dominant_color
                    base_color = await get_dominant_color(
                        player.now_playing.thumbnail, self.http_session
                    )
                    player._cached_base_color = base_color

                guild_vc = self.get_guild(guild_id).voice_client if self.get_guild(guild_id) else None
                is_paused = bool(guild_vc and guild_vc.is_paused())

                # Perf-1 UX: When paused the progress knob doesn't move — skip
                # the edit unless something else changed (the embed_theme or
                # now_playing changed). This avoids Discord API churn and prevents
                # the embed from flickering every 7 s while paused.
                if is_paused:
                    continue

                color = animated_embed_color(base_color, player.elapsed_seconds)
                embed = now_playing_embed(player, color, self.user, paused=is_paused, theme=getattr(player, "embed_theme", "classic"))
                msg   = player.now_playing_msg
                if hasattr(msg, "edit"):
                    await msg.edit(embed=embed)
            except discord.NotFound:
                # Bug 10: message was deleted — clear ref to stop repeated 404s
                player.now_playing_msg    = None
                player.now_playing_msg_id = None
            except Exception:
                # Bug 10: on any unexpected error, clear the msg ref too so the
                # next tick doesn't retry a potentially broken message object.
                player.now_playing_msg = None

    @_np_refresh.before_loop
    async def _before_np_refresh(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def _cache_prune(self) -> None:
        """Prune expired yt-dlp cache entries and media cache every 30 minutes.

        Auto-recovery: if the cache raises an unexpected error (corruption),
        it is cleared entirely and a warning is logged so the next prune runs clean.
        """
        try:
            raw_n, search_n = await self.youtube.prune_cache()
            thumb_n, meta_n = prune_media_caches()  # Tier-S+ F14
            if raw_n or search_n or thumb_n or meta_n:
                logger.debug(
                    "Cache prune: yt-dlp raw=%d search=%d  media thumb=%d meta=%d",
                    raw_n, search_n, thumb_n, meta_n,
                )
        except Exception as exc:
            # Auto-recovery: cache may be corrupted — clear and rebuild
            logger.warning(
                "🔧 Cache prune raised %s (%s) — clearing all caches for auto-recovery.",
                type(exc).__name__, exc,
            )
            try:
                async with self.youtube._cache_lock:
                    self.youtube._cache.clear()
                async with self.youtube._search_cache_lock:
                    self.youtube._search_cache.clear()
                prune_media_caches()
            except Exception as clear_exc:
                logger.error("Cache auto-recovery failed: %s", clear_exc)

        # Perf-1: log all cache metrics at DEBUG level every 30 min
        try:
            from utils.color_thief import color_cache_stats
            from core.lru_cache import combined_stats as _lru_stats
            lru = await _lru_stats()
            color = color_cache_stats()
            cfg   = self.db.config_cache_stats()
            logger.debug(
                "Cache metrics — color: hits=%d misses=%d evictions=%d hit_rate=%.1f%% | "
                "config: hits=%d misses=%d hit_rate=%.1f%% size=%d | "
                "lru metadata: hits=%d misses=%d evictions=%d | "
                "lru thumbnail: hits=%d misses=%d evictions=%d | "
                "lru search: hits=%d misses=%d evictions=%d",
                color["hits"], color["misses"], color["evictions"], color["hit_rate"] * 100,
                cfg["hits"], cfg["misses"], cfg["hit_rate"] * 100, cfg["size"],
                lru["metadata"]["hits"], lru["metadata"]["misses"], lru["metadata"]["evictions"],
                lru["thumbnail"]["hits"], lru["thumbnail"]["misses"], lru["thumbnail"]["evictions"],
                lru["search"]["hits"],    lru["search"]["misses"],    lru["search"]["evictions"],
            )
        except Exception as exc:
            logger.warning("Cache metrics logging error: %s", exc)

    @_cache_prune.before_loop
    async def _before_cache_prune(self) -> None:
        await self.wait_until_ready()


    @tasks.loop(hours=24)
    async def _analytics_prune(self) -> None:
        """Prune analytics older than 30 days."""
        try:
            pruned = await self.db.prune_analytics(days=30)
            logger.debug("Analytics prune: %d rows removed.", pruned)
        except Exception as exc:
            logger.warning("Analytics prune error: %s", exc)

    @_analytics_prune.before_loop
    async def _before_analytics_prune(self) -> None:
        await self.wait_until_ready()

    # Tier-S+ Feature 11: Session State Heartbeat ─────────────────────────────

    @tasks.loop(seconds=60)
    async def _session_heartbeat(self) -> None:
        """
        Save full playback state every 60 seconds for each active guild.
        This means a crash loses at most 60s of position data.
        """
        for guild_id, player in list(self._players.items()):
            if not player.now_playing:
                continue
            guild = self.get_guild(guild_id)
            vc    = guild.voice_client if guild else None
            if not vc:
                continue
            try:
                await self.db.save_session_state(
                    guild_id        = guild_id,
                    channel_id      = vc.channel.id,
                    text_channel_id = player.text_channel.id if player.text_channel else 0,
                    now_playing     = player.now_playing,
                    elapsed_secs    = player.elapsed_seconds,
                    queue           = player.queue,
                    loop_mode       = player.loop_mode.value,
                    volume          = player.volume,
                    effects         = [e.value for e in player.effects],
                )
            except Exception as exc:
                logger.debug("Session heartbeat error guild %d: %s", guild_id, exc)

    @_session_heartbeat.before_loop
    async def _before_session_heartbeat(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=60)
    async def _memory_pressure(self) -> None:
        """Check bot process RSS and evict caches if pressure thresholds exceeded (F32).

        Monitors OWN process RSS (not system-wide RAM) to avoid false positives
        when other apps (Chrome, VS Code, etc.) consume memory on the same host.
        """
        try:
            result = await check_memory_pressure()
            if result["level"] >= 2:
                logger.warning(
                    "Memory pressure action: %s | bot RSS=%.0fMiB | system RAM=%.1f%% | freed %d entries",
                    result["action"], result["rss_mb"], result["sys_ram_pct"], result["freed"],
                )
        except Exception as exc:
            logger.debug("Memory pressure check error: %s", exc)

    @_memory_pressure.before_loop
    async def _before_memory_pressure(self) -> None:
        await self.wait_until_ready()

    # ── Stability: New background tasks ───────────────────────────────────────

    @tasks.loop(minutes=5)
    async def _metrics_snapshot(self) -> None:
        """Collect and log a runtime metrics snapshot every 5 minutes."""
        if self.metrics:
            try:
                await self.metrics.collect()
            except Exception as exc:
                logger.debug("Metrics snapshot error: %s", exc)

    @_metrics_snapshot.before_loop
    async def _before_metrics_snapshot(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def _memory_leak_detect(self) -> None:
        """Take a memory snapshot every 30 minutes and warn on anomalous growth."""
        if self.mem_leak_detector:
            try:
                # Gather extra stats for the snapshot log line
                extra: dict = {
                    "guilds":  len(self.guilds),
                    "players": len(self._players),
                    "queued":  sum(len(p) for p in self._players.values()),
                }
                try:
                    from core.lru_cache import combined_stats
                    lru = await combined_stats()
                    extra["lru_entries"] = sum(s.get("size", 0) for s in lru.values())
                except Exception:
                    pass
                await self.mem_leak_detector.tick(extra_stats=extra)
            except Exception as exc:
                logger.debug("Memory leak detector error: %s", exc)

    @_memory_leak_detect.before_loop
    async def _before_memory_leak_detect(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=60)
    async def _dead_task_watchdog(self) -> None:
        """Check all registered background task loops and restart any that have died."""
        if self.task_watchdog:
            try:
                await self.task_watchdog.tick()
            except Exception as exc:
                logger.debug("Dead task watchdog error: %s", exc)

    @_dead_task_watchdog.before_loop
    async def _before_dead_task_watchdog(self) -> None:
        await self.wait_until_ready()

    # ── Error handlers ────────────────────────────────────────────────────────

    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        from utils.embeds import error_embed
        # Classify the exception and log at appropriate level
        kind = log_with_kind(error, logger, context="app_command", include_traceback=True)
        embed = error_embed("Unexpected Error", str(error)[:200])
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

        # Only forward FATAL / RECOVERABLE errors to dev channel (not EXPECTED)
        if kind != ExceptionKind.EXPECTED:
            from utils.error_handler import forward_to_dev_channel
            await forward_to_dev_channel(self, error, interaction)

    async def on_error(self, event: str, *args, **kwargs) -> None:
        import sys
        exc = sys.exc_info()[1]
        if exc is not None:
            log_with_kind(exc, logger, context=f"event:{event}")
        else:
            logger.error("Unhandled error in event '%s':", event, exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import sys
    # Pre-login validation — runs BEFORE any network connection to Discord
    report = validate_pre_login(print_report=True)
    if report.has_fatal:
        sys.exit(1)

    bot = MusicBot()
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
