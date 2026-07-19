# -*- coding: utf-8 -*-
"""
cogs/music.py — Core music playback commands for Music Bot V3.

Commands: /join /leave /play /search /pause /resume /skip /stop /nowplaying

V3 Changes:
  - DJ permission check via _check_dj()
  - Auto-playlist trigger when queue empties
  - Progress bar in /nowplaying (live update every 30s via background task)
  - Request channel support: play triggered from on_message (handled in main.py)
  - Circuit breaker on YouTube + Spotify calls
  - Predictive prefetch scheduled ~15s before track end
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core.circuit_breaker import CircuitBreakerOpen
from core.validator import validate_url, validate_search_query
from models.track import Track
from utils.embeds import (
    error_embed, success_embed, info_embed,
    now_playing_embed, track_added_embed, playlist_added_embed,
    search_results_embed, auto_playlist_embed,
)
from utils.views import MusicControlView, SearchSelectView
from utils.rate_limiter import RateLimiter
from utils.error_handler import (
    notify_playback_error, voice_connection_error_embed, dj_required_embed
)
from utils.color_thief import get_dominant_color

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# ── DJ-only commands (need DJ role or admin) ──────────────────────────────────
_DJ_COMMANDS = {"stop", "skip", "shuffle", "clear", "remove", "move", "volume", "effects"}


class MusicCog(commands.Cog, name="Music"):
    """Core music playback commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot          = bot
        self.rate_limiter = RateLimiter(max_calls=5, window=10.0)

    # ── Permission helpers ────────────────────────────────────────────────────

    async def _check_dj(self, interaction: discord.Interaction, command_name: str = "") -> bool:
        """
        Return True if the user has DJ permission.
        DJ permission = has DJ role OR is admin OR no DJ role configured.
        """
        if command_name and command_name not in _DJ_COMMANDS:
            return True

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True  # No DJ role set — anyone can use

        member = interaction.user
        if member.guild_permissions.administrator:
            return True

        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True

        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    async def _ensure_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Join or return existing voice client."""
        guild = interaction.guild
        vc    = guild.voice_client

        if vc and vc.is_connected():
            return vc

        if not interaction.user.voice:
            await interaction.followup.send(
                embed=error_embed("Not in a Voice Channel", "Join a voice channel first."),
                ephemeral=True,
            )
            return None

        channel = interaction.user.voice.channel
        try:
            vc = await channel.connect(timeout=10.0, reconnect=True)
            player = self.bot.get_player(interaction.guild_id)
            player.last_channel_id        = channel.id
            player.text_channel           = interaction.channel
            player.intentional_disconnect = False  # new connection — re-enable auto-reconnect
            return vc
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Connection Timeout", "Could not connect to your voice channel."),
                ephemeral=True,
            )
            return None
        except discord.ClientException as exc:
            await interaction.followup.send(
                embed=error_embed("Connection Error", str(exc)), ephemeral=True
            )
            return None

    async def _try_reconnect(self, guild_id: int) -> Optional[discord.VoiceClient]:
        """Self-healing voice reconnect with exponential backoff."""
        player = self.bot.get_player(guild_id)

        # Don't reconnect if the disconnect was intentional (/stop, /leave)
        if player.intentional_disconnect:
            logger.debug("guild %d: skipping reconnect — intentional disconnect.", guild_id)
            return None

        guild  = self.bot.get_guild(guild_id)
        if not guild or not player.last_channel_id:
            return None

        channel = guild.get_channel(player.last_channel_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            return None

        for attempt in range(1, config.RECONNECT_ATTEMPTS + 1):
            delay = config.RECONNECT_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("Voice reconnect attempt %d/%d for guild %d in %.0fs…",
                        attempt, config.RECONNECT_ATTEMPTS, guild_id, delay)
            await asyncio.sleep(delay)
            try:
                vc = await channel.connect(timeout=10.0, reconnect=True)
                logger.info("✅ Voice reconnected for guild %d on attempt %d", guild_id, attempt)
                return vc
            except Exception as exc:
                logger.warning("Reconnect attempt %d failed for guild %d: %s", attempt, guild_id, exc)

        logger.error("Voice reconnect failed for guild %d after %d attempts.", guild_id, config.RECONNECT_ATTEMPTS)
        if player.text_channel:
            try:
                await player.text_channel.send(
                    embed=voice_connection_error_embed(channel.name, config.RECONNECT_ATTEMPTS)
                )
            except Exception:
                pass
        return None

    # ── Playback core ─────────────────────────────────────────────────────────

    async def _play_next(self, guild_id: int, *, skip_depth: int = 0) -> None:
        """
        Pop the next track from the player queue and start playback.
        Handles: auto-skip broken tracks, circuit breaker, prefetch,
                 auto-playlist when queue empties.
        """
        player = self.bot.get_player(guild_id)
        guild  = self.bot.get_guild(guild_id)
        if not guild:
            return

        vc: Optional[discord.VoiceClient] = guild.voice_client
        if not vc or not vc.is_connected():
            vc = await self._try_reconnect(guild_id)
            if not vc:
                player.reset()
                return

        if vc.is_playing():
            logger.debug("guild %d: _play_next while already playing — ignoring.", guild_id)
            return

        await player.finish_track()

        next_track = await player.dequeue()

        if not next_track:
            # ── Auto-playlist: fill from history when queue empty ──────────────
            cfg = await self.bot.db.get_server_config(guild_id)
            if cfg.auto_playlist or player.auto_playlist_mode:
                recent = await self.bot.db.get_recent_tracks_for_autoplaylist(
                    guild_id, limit=cfg.auto_playlist_size * 3
                )
                seeds = recent[:cfg.auto_playlist_size]
                if seeds:
                    await player.extend(seeds)
                    if player.text_channel:
                        try:
                            await player.text_channel.send(
                                embed=auto_playlist_embed(len(seeds)), delete_after=30
                            )
                        except Exception:
                            pass
                    next_track = await player.dequeue()

        if not next_track:
            player.idle_since = discord.utils.utcnow()
            return

        # ── Resolve stream URL (circuit breaker guarded) ──────────────────────
        try:
            stream_url = await self.bot.yt_breaker.call(
                self.bot.youtube.get_stream_url,
                next_track.url,
                next_track,
            )
        except CircuitBreakerOpen:
            if player.text_channel:
                try:
                    await player.text_channel.send(
                        embed=error_embed("Service Busy", "YouTube API circuit breaker is OPEN. Try again later."),
                        delete_after=30,
                    )
                except Exception:
                    pass
            player.reset()
            return
        except Exception as exc:
            logger.warning("Stream URL resolve failed for '%s': %s", next_track.title[:50], exc)
            if skip_depth < config.SKIP_ERROR_LIMIT:
                await notify_playback_error(self.bot, guild_id, next_track.title, exc)
                return await self._play_next(guild_id, skip_depth=skip_depth + 1)
            player.reset()
            return

        if not stream_url:
            logger.warning("No stream URL for '%s'", next_track.title[:50])
            if skip_depth < config.SKIP_ERROR_LIMIT:
                return await self._play_next(guild_id, skip_depth=skip_depth + 1)
            player.reset()
            return

        # ── Build FFmpeg options ───────────────────────────────────────────────
        cfg_server = await self.bot.db.get_server_config(guild_id)
        ffmpeg_opts = self.bot.audio_processor.build_ffmpeg_options(
            effects = player.effects,
            volume  = player.volume,
            quality = cfg_server.audio_quality,
        )

        # ── Start playback ────────────────────────────────────────────────────
        def after_play(error: Optional[Exception]) -> None:
            if error:
                logger.error("Playback error in guild %d: %s", guild_id, error)
            asyncio.run_coroutine_threadsafe(
                self._play_next(guild_id), self.bot.loop
            )

        try:
            await self.bot.audio_backend.play(vc, stream_url, ffmpeg_opts, after_play)
        except Exception as exc:
            logger.error("FFmpeg start failed: %s", exc)
            if skip_depth < config.SKIP_ERROR_LIMIT:
                return await self._play_next(guild_id, skip_depth=skip_depth + 1)
            return

        # ── Update player state ───────────────────────────────────────────────
        import datetime
        player.now_playing     = next_track
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc)
        player.idle_since      = None

        # ── Record analytics ───────────────────────────────────────────────────
        asyncio.create_task(
            self.bot.db.log_event(guild_id, "track_play", {"title": next_track.title, "url": next_track.url})
        )

        # ── Send now-playing embed ────────────────────────────────────────────
        if player.text_channel:
            try:
                color = await get_dominant_color(next_track.thumbnail, self.bot.http_session)
                embed = now_playing_embed(player, color, self.bot.user)
                view  = MusicControlView(self.bot, guild_id)
                msg   = await player.text_channel.send(embed=embed, view=view)
                player.now_playing_msg    = msg
                player.now_playing_msg_id = msg.id
            except Exception as exc:
                logger.warning("Could not send now-playing embed: %s", exc)

        # ── Schedule predictive prefetch for next track ───────────────────────
        self._schedule_prefetch(guild_id, next_track)

    def _schedule_prefetch(self, guild_id: int, current_track: Track) -> None:
        """Schedule background prefetch ~15s before current track ends."""
        player = self.bot.get_player(guild_id)
        player.cancel_prefetch()

        queue = player.queue
        if not queue:
            return

        next_track = queue[0]
        delay = max(0, (current_track.duration or 60) - 15)

        async def _prefetch_after_delay():
            await asyncio.sleep(delay)
            await self.bot.youtube.prefetch_stream_url(next_track)

        player._prefetch_task = asyncio.create_task(_prefetch_after_delay())

    # ── Public helper: play_track ──────────────────────────────────────────────

    async def play_track(
        self,
        interaction: discord.Interaction,
        track:       Track,
    ) -> None:
        """
        Enqueue a single track and start playback if not already playing.
        Called from music, search, favorites, and request-channel handler.
        """
        vc = await self._ensure_voice(interaction)
        if not vc:
            return

        player = self.bot.get_player(interaction.guild_id)

        track.requested_by_id   = interaction.user.id
        track.requested_by_name = interaction.user.display_name

        pos = await player.enqueue(track)

        # Save queue to DB immediately (write-ahead)
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        asyncio.create_task(
            self.bot.db.save_queue(
                interaction.guild_id,
                vc.channel.id,
                player.queue,
            )
        )
        asyncio.create_task(
            self.bot.db.add_search_history(interaction.guild_id, interaction.user.id, track.title)
        )

        color = await get_dominant_color(track.thumbnail, self.bot.http_session)
        if vc.is_playing() or vc.is_paused():
            await interaction.followup.send(
                embed=track_added_embed(track, pos, color, interaction.user)
            )
        else:
            await interaction.followup.send(
                embed=track_added_embed(track, pos, color, interaction.user)
            )
            await self._play_next(interaction.guild_id)

    # ── Slash Commands ────────────────────────────────────────────────────────

    @app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = await self._ensure_voice(interaction)
        if vc:
            await interaction.followup.send(
                embed=success_embed("Joined", f"Connected to **{vc.channel.name}**"), ephemeral=True
            )

    @app_commands.command(name="leave", description="Disconnect and clear queue")
    async def leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction, "stop"):
            return
        player = self.bot.get_player(interaction.guild_id)
        player.reset()
        player.intentional_disconnect = True  # must come AFTER reset() so it sticks
        await self.bot.db.clear_queue(interaction.guild_id)
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        await interaction.followup.send(embed=success_embed("Disconnected", "Queue cleared."))

    @app_commands.command(name="play", description="Play a YouTube URL, Spotify URL, or search query")
    @app_commands.describe(query="YouTube URL, Spotify URL, playlist, or search terms")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        if self.rate_limiter.is_rate_limited(interaction.guild_id, interaction.user.id):
            from utils.error_handler import rate_limited_embed
            await interaction.followup.send(embed=rate_limited_embed(
                self.rate_limiter.remaining(interaction.guild_id, interaction.user.id)
            ), ephemeral=True)
            return

        query = query.strip()

        # ── Spotify URL ──────────────────────────────────────────────────────
        if self.bot.spotify.is_spotify_url(query):
            tracks = await self.bot.sp_breaker.call(
                self.bot.spotify.resolve,
                query,
                self.bot.http_session,
                self.bot.youtube,
                config.MAX_PLAYLIST_TRACKS,
            )
            if not tracks:
                await interaction.followup.send(
                    embed=error_embed("Spotify Error", "Could not resolve Spotify URL."), ephemeral=True
                )
                return
            vc = await self._ensure_voice(interaction)
            if not vc:
                return
            player = self.bot.get_player(interaction.guild_id)
            for t in tracks:
                t.requested_by_id   = interaction.user.id
                t.requested_by_name = interaction.user.display_name
            await player.extend(tracks)
            asyncio.create_task(self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue))
            await interaction.followup.send(embed=playlist_added_embed(len(tracks)))
            if not vc.is_playing():
                await self._play_next(interaction.guild_id)
            return

        # ── YouTube playlist ──────────────────────────────────────────────────
        if self.bot.youtube.is_playlist_url(query):
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
            tracks = await self.bot.youtube.get_playlist(query, cfg.max_playlist_tracks)
            if not tracks:
                await interaction.followup.send(
                    embed=error_embed("Playlist Error", "Could not extract playlist."), ephemeral=True
                )
                return
            vc = await self._ensure_voice(interaction)
            if not vc:
                return
            player = self.bot.get_player(interaction.guild_id)
            for t in tracks:
                t.requested_by_id   = interaction.user.id
                t.requested_by_name = interaction.user.display_name
            await player.extend(tracks)
            asyncio.create_task(self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue))
            await interaction.followup.send(embed=playlist_added_embed(len(tracks)))
            if not vc.is_playing():
                await self._play_next(interaction.guild_id)
            return

        # ── YouTube URL ───────────────────────────────────────────────────────
        if self.bot.youtube.is_youtube_url(query):
            is_safe, reason = await validate_url(query, self.bot.http_session)
            if not is_safe:
                await interaction.followup.send(
                    embed=error_embed("Blocked", reason), ephemeral=True
                )
                return
            try:
                track = await self.bot.yt_breaker.call(self.bot.youtube.get_track, query)
            except CircuitBreakerOpen:
                await interaction.followup.send(
                    embed=error_embed("Service Busy", "YouTube API is temporarily unavailable."), ephemeral=True
                )
                return
            if not track:
                await interaction.followup.send(
                    embed=error_embed("Not Found", "Could not retrieve that video."), ephemeral=True
                )
                return
            await self.play_track(interaction, track)
            return

        # ── Search query ──────────────────────────────────────────────────────
        is_safe, reason = validate_search_query(query)
        if not is_safe:
            await interaction.followup.send(embed=error_embed("Blocked", reason), ephemeral=True)
            return

        try:
            tracks = await self.bot.yt_breaker.call(self.bot.youtube.search, query, 1)
        except CircuitBreakerOpen:
            await interaction.followup.send(
                embed=error_embed("Service Busy", "YouTube API is temporarily unavailable."), ephemeral=True
            )
            return

        if not tracks:
            await interaction.followup.send(
                embed=error_embed("No Results", f"No results for **{query}**."), ephemeral=True
            )
            return
        await self.play_track(interaction, tracks[0])

    @play.autocomplete("query")
    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        suggestions = await self.bot.db.get_search_history(
            interaction.guild_id, prefix=current, limit=25
        )
        return [app_commands.Choice(name=s[:100], value=s) for s in suggestions]

    @app_commands.command(name="search", description="Search YouTube and choose from results")
    @app_commands.describe(query="Search terms")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        is_safe, reason = validate_search_query(query)
        if not is_safe:
            await interaction.followup.send(embed=error_embed("Blocked", reason), ephemeral=True)
            return

        try:
            tracks = await self.bot.yt_breaker.call(self.bot.youtube.search, query, 10)
        except CircuitBreakerOpen:
            await interaction.followup.send(
                embed=error_embed("Service Busy", "YouTube is temporarily unavailable."), ephemeral=True
            )
            return

        if not tracks:
            await interaction.followup.send(
                embed=error_embed("No Results", f"No results for **{query}**."), ephemeral=True
            )
            return

        embed = search_results_embed(query, tracks)

        async def on_select(inter: discord.Interaction, track: Track) -> None:
            await inter.response.defer()
            await self.play_track(inter, track)

        view = SearchSelectView(self.bot, interaction.guild_id, tracks, on_select)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.followup.send(embed=success_embed("Paused"), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Not Playing"), ephemeral=True)

    @app_commands.command(name="resume", description="Resume paused playback")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.followup.send(embed=success_embed("Resumed"), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Not Paused"), ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction, "skip"):
            return
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.followup.send(embed=success_embed("Skipped ⏭"), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Nothing to Skip"), ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback, clear queue, and disconnect")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction, "stop"):
            return
        player = self.bot.get_player(interaction.guild_id)
        player.reset()
        player.intentional_disconnect = True  # must come AFTER reset() so it sticks
        await self.bot.db.clear_queue(interaction.guild_id)
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        await interaction.followup.send(embed=success_embed("Stopped ⏹", "Queue cleared and disconnected."))

    @app_commands.command(name="nowplaying", description="Show the current track with progress bar")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(embed=info_embed("Nothing Playing", "Queue is empty."))
            return
        color = await get_dominant_color(player.now_playing.thumbnail, self.bot.http_session)
        embed = now_playing_embed(player, color, self.bot.user)
        view  = MusicControlView(self.bot, interaction.guild_id)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(MusicCog(bot))
