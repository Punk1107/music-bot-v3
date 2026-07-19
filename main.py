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

        # ── Per-guild player registry ─────────────────────────────────────────
        self._players:   dict[int, GuildPlayer] = {}

        # ── Shared HTTP session (created in setup_hook) ───────────────────────
        self.http_session: Optional[aiohttp.ClientSession] = None

        # ── Timing ────────────────────────────────────────────────────────────
        self.start_time: datetime = datetime.now(timezone.utc)

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

        # Start background tasks
        self._idle_check.start()
        self._queue_save.start()
        self._np_refresh.start()
        self._cache_prune.start()
        self._analytics_prune.start()

        logger.info("✅ Setup complete. Bot is starting…")

    async def close(self) -> None:
        """Graceful shutdown: save queues, stop services, close connections."""
        logger.info("Shutting down Music Bot V3…")

        # Stop background tasks
        for t in [self._idle_check, self._queue_save, self._np_refresh,
                  self._cache_prune, self._analytics_prune]:
            t.cancel()

        # Persist all active queues
        for guild_id, player in self._players.items():
            if player.queue or player.now_playing:
                guild = self.get_guild(guild_id)
                vc    = guild.voice_client if guild else None
                channel_id = vc.channel.id if vc else (player.last_channel_id or 0)
                try:
                    all_tracks = ([player.now_playing] if player.now_playing else []) + player.queue
                    await self.db.save_queue(guild_id, channel_id, all_tracks)
                except Exception as exc:
                    logger.warning("Queue save on shutdown for guild %d: %s", guild_id, exc)

        # Disconnect all voice clients
        for guild in self.guilds:
            vc = guild.voice_client
            if vc and vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass

        # Stop webserver
        await self.webserver.stop()

        # Close DB
        await self.db.close()

        # Close HTTP session
        if self.http_session:
            await self.http_session.close()

        await super().close()
        logger.info("✅ Shutdown complete.")

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
        for guild in self.guilds:
            try:
                tracks = await self.db.load_queue(guild.id)
                if tracks:
                    player = self.get_player(guild.id)
                    await player.extend(tracks)
                    logger.info("Restored %d track(s) for guild %d", len(tracks), guild.id)
            except Exception as exc:
                logger.warning("Queue restore error for guild %d: %s", guild.id, exc)

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
        """Track idle state: if bot is alone in voice, start idle timer."""
        if member.bot:
            return

        guild  = member.guild
        player = self.get_player(guild.id)
        vc     = guild.voice_client

        if not vc or not vc.channel:
            return

        non_bot_members = [m for m in vc.channel.members if not m.bot]
        if len(non_bot_members) == 0:
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
            if vc and vc.is_playing():
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
        """Disconnect guilds that have been idle longer than their configured timeout."""
        now = datetime.now(timezone.utc)
        for guild_id, player in list(self._players.items()):
            if player.now_playing or not player.idle_since:
                continue
            guild = self.get_guild(guild_id)
            if not guild:
                continue
            vc = guild.voice_client
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
        """Refresh now-playing embed progress bar every 7 seconds."""
        for guild_id, player in self._players.items():
            if not player.now_playing or not player.now_playing_msg:
                continue
            try:
                from utils.embeds import now_playing_embed
                from utils.color_thief import animated_embed_color, get_dominant_color
                base_color = await get_dominant_color(
                    player.now_playing.thumbnail, self.http_session
                )
                color = animated_embed_color(base_color, player.elapsed_seconds)
                embed = now_playing_embed(player, color, self.user)
                msg   = player.now_playing_msg
                if hasattr(msg, "edit"):
                    await msg.edit(embed=embed)
            except discord.NotFound:
                player.now_playing_msg = None
            except Exception:
                pass

    @_np_refresh.before_loop
    async def _before_np_refresh(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def _cache_prune(self) -> None:
        """Prune expired yt-dlp cache entries every 30 minutes."""
        raw_n, search_n = await self.youtube.prune_cache()
        if raw_n or search_n:
            logger.debug("Cache prune: removed %d raw, %d search entries.", raw_n, search_n)

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

    # ── Error handlers ────────────────────────────────────────────────────────

    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        from utils.embeds import error_embed
        logger.error("App command error: %s", error, exc_info=True)
        embed = error_embed("Unexpected Error", str(error)[:200])
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

        from utils.error_handler import forward_to_dev_channel
        await forward_to_dev_channel(self, error, interaction)

    async def on_error(self, event: str, *args, **kwargs) -> None:
        logger.error("Unhandled error in event '%s':", event, exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    bot = MusicBot()
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
