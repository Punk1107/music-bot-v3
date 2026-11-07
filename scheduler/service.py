# -*- coding: utf-8 -*-
"""
scheduler/service.py — Business logic and runner for Server Music Scheduler & Alarm Clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import TYPE_CHECKING, Optional

import discord

from scheduler.models import ScheduledTask

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])(:[0-5][0-9])?$")


class MusicSchedulerService:
    """Service for managing and executing server scheduled playback tasks."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    @staticmethod
    def normalize_time(time_str: str) -> str:
        """Validate and normalize time string to HH:MM:00 format."""
        cleaned = time_str.strip()
        m = TIME_PATTERN.match(cleaned)
        if not m:
            raise ValueError(f"Invalid time format '{time_str}'. Please use HH:MM (e.g., 08:00 or 17:30).")
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}:00"

    async def add_task(
        self,
        guild_id: int,
        voice_channel_id: int,
        text_channel_id: int,
        user_id: int,
        time_str: str,
        query: str,
        repeat_mode: str = "daily",
    ) -> ScheduledTask:
        """Add a new scheduled playback task to MySQL/SQLite."""
        norm_time = self.normalize_time(time_str)
        rep = repeat_mode.lower().strip()
        if rep not in ("daily", "weekdays", "once", "weekends"):
            rep = "daily"

        task_id = await self.bot.db.create_scheduled_task(
            guild_id=guild_id,
            voice_channel_id=voice_channel_id,
            text_channel_id=text_channel_id,
            user_id=user_id,
            scheduled_time=norm_time,
            query=query,
            days_of_week=rep,
        )

        return ScheduledTask(
            id=task_id,
            guild_id=guild_id,
            voice_channel_id=voice_channel_id,
            text_channel_id=text_channel_id,
            user_id=user_id,
            scheduled_time=norm_time[:5],
            days_of_week=rep,
            query=query,
            is_active=True,
        )

    async def list_tasks(self, guild_id: int) -> list[ScheduledTask]:
        """List all scheduled tasks for a guild."""
        rows = await self.bot.db.list_scheduled_tasks(guild_id)
        return [ScheduledTask.from_dict(r) for r in rows]

    async def delete_task(self, guild_id: int, task_id: int) -> bool:
        """Delete a scheduled task."""
        return await self.bot.db.delete_scheduled_task(guild_id, task_id)

    async def check_and_run_due_tasks(self, now_utc: Optional[datetime] = None) -> list[int]:
        """
        Poll database for due tasks, claim them atomically, and trigger playback.
        Returns list of executed task IDs.
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        claimed = await self.bot.db.claim_due_scheduled_tasks(now_utc=now_utc)
        if not claimed:
            return []

        executed_ids: list[int] = []
        for task_dict in claimed:
            task = ScheduledTask.from_dict(task_dict)
            try:
                await self._execute_task(task)
                executed_ids.append(task.id)
            except Exception as exc:
                logger.error("Failed executing scheduled task #%d in guild %d: %s", task.id, task.guild_id, exc)
            finally:
                # Release worker lock
                try:
                    await self.bot.db.release_scheduled_task_lock(task.id)
                except Exception:
                    pass

                # If repeat_mode is once and execution succeeded, delete task
                if task.days_of_week == "once" and task.id in executed_ids:
                    try:
                        await self.bot.db.delete_scheduled_task(task.guild_id, task.id)
                    except Exception:
                        pass

        return executed_ids

    async def _execute_task(self, task: ScheduledTask) -> None:
        """Connect to voice, enqueue/play track, and post announcement."""
        guild = self.bot.get_guild(task.guild_id)
        if not guild:
            logger.warning("Scheduled task #%d skipped: Guild %d not found", task.id, task.guild_id)
            return

        vc_channel = guild.get_channel(task.voice_channel_id)
        if not vc_channel or not isinstance(vc_channel, (discord.VoiceChannel, discord.StageChannel)):
            logger.warning("Scheduled task #%d skipped: VoiceChannel %d not found", task.id, task.voice_channel_id)
            return

        text_channel = guild.get_channel(task.text_channel_id)

        # 1. Ensure voice connection
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            try:
                vc = await vc_channel.connect(timeout=15.0)
            except Exception as e:
                logger.error("Scheduler voice connect error in guild %d: %s", task.guild_id, e)
                return
        elif vc.channel and vc.channel.id != vc_channel.id:
            try:
                await vc.move_to(vc_channel)
            except Exception as e:
                logger.error("Scheduler move voice error in guild %d: %s", task.guild_id, e)

        # 2. Search / Fetch track
        track = None
        try:
            sources_router = getattr(self.bot, "sources_router", None)
            spotify = getattr(self.bot, "spotify", None)
            if spotify and getattr(spotify, "available", False) and spotify.is_spotify_url(task.query):
                sp_breaker = getattr(self.bot, "sp_breaker", None)
                if sp_breaker:
                    sp_tracks = await sp_breaker.call(
                        spotify.resolve,
                        task.query,
                        self.bot.http_session,
                        self.bot.youtube,
                        50,
                    )
                else:
                    sp_tracks = await spotify.resolve(
                        task.query,
                        self.bot.http_session,
                        self.bot.youtube,
                        50,
                    )
                track = sp_tracks[0] if sp_tracks else None
            elif sources_router and sources_router.is_supported(task.query):
                sr_tracks = await sources_router.resolve(task.query)
                if isinstance(sr_tracks, tuple):
                    sr_tracks = sr_tracks[0]
                track = sr_tracks[0] if sr_tracks else None
            elif hasattr(self.bot, "youtube") and self.bot.youtube.is_youtube_url(task.query):
                track = await self.bot.yt_breaker.call(self.bot.youtube.get_track, task.query)
            elif hasattr(self.bot, "youtube"):
                results = await self.bot.yt_breaker.call(self.bot.youtube.search, task.query, 1)
                track = results[0] if results else None
        except Exception as exc:
            logger.warning("Scheduler track search error: %s", exc)

        if not track:
            if not task.query.startswith(("http://", "https://")):
                logger.warning("Scheduled task #%d skipped: could not resolve query '%s'", task.id, task.query[:60])
                return
            from models.track import Track
            track = Track(
                title=task.query[:60],
                url=task.query,
                duration=0,
                uploader="Scheduled Alarm",
            )

        track.requested_by_id = task.user_id
        track.requested_by_name = "Music Scheduler"

        # 3. Enqueue & Play
        player = self.bot.get_player(task.guild_id)
        player.intentional_disconnect = False
        player.last_channel_id = vc_channel.id
        if text_channel and isinstance(text_channel, discord.TextChannel):
            player.text_channel = text_channel

        await player.enqueue(track)
        if hasattr(self.bot, "db") and hasattr(self.bot.db, "save_queue") and hasattr(self.bot, "track_task"):
            self.bot.track_task(self.bot.db.save_queue(task.guild_id, vc_channel.id, player.queue))

        music_cog = self.bot.cogs.get("Music")
        if music_cog and not vc.is_playing() and not vc.is_paused():
            await music_cog._play_next(task.guild_id)
        elif hasattr(self.bot, "update_clean_board") and hasattr(self.bot, "track_task"):
            self.bot.track_task(self.bot.update_clean_board(task.guild_id))

        # 4. Announce alarm in text channel
        if text_channel and isinstance(text_channel, discord.TextChannel):
            embed = discord.Embed(
                title="⏰ Server Music Scheduler & Alarm Clock",
                description=f"Playing scheduled music: **[{track.title}]({track.url})**\n"
                            f"• Scheduled Time: `{task.scheduled_time}`\n"
                            f"• Repeat: `{task.days_of_week.capitalize()}`\n"
                            f"• Channel: <#{task.voice_channel_id}>",
                color=0x5865F2,
            )
            embed.set_footer(text=f"Task #{task.id} · Powered by MySQL Scheduler")
            try:
                await text_channel.send(embed=embed)
            except Exception:
                pass
