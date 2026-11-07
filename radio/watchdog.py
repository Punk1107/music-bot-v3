# -*- coding: utf-8 -*-
"""radio/watchdog.py — 24/7 Web Radio Stream Watchdog and Auto-Reconnect (Gen 5.0)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Optional

from models.track import Track

if TYPE_CHECKING:
    import discord
    from main import MusicBot

logger = logging.getLogger(__name__)


@dataclass
class RadioSession:
    station_id: str
    station_name: str
    stream_url: str
    started_at: datetime
    reconnect_count: int = 0


class RadioWatchdog:
    """
    Monitors 24/7 radio streams across active guilds.
    Detects unexpected audio pipe stalls / stream disconnects and seamlessly re-connects.
    """

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self._sessions: dict[int, RadioSession] = {}
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop(), name="radio_24_7_watchdog")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._sessions.clear()

    def register(self, guild_id: int, station_id: str, name: str, url: str) -> None:
        self._sessions[guild_id] = RadioSession(
            station_id=station_id,
            station_name=name,
            stream_url=url,
            started_at=datetime.now(timezone.utc),
        )
        player = self.bot.get_player(guild_id)
        player.is_radio_mode = True
        logger.info("Registered 24/7 Radio Session for guild %d: '%s' (%s)", guild_id, name, url)

    def unregister(self, guild_id: int) -> None:
        if guild_id in self._sessions:
            del self._sessions[guild_id]
        if hasattr(self.bot, "get_player"):
            try:
                player = self.bot.get_player(guild_id)
                if player is not None:
                    player.is_radio_mode = False
            except Exception as exc:
                logger.error("watchdog.unregister failed for guild %d: %s", guild_id, exc)

    def is_active(self, guild_id: int) -> bool:
        return guild_id in self._sessions

    def get_session(self, guild_id: int) -> Optional[RadioSession]:
        return self._sessions.get(guild_id)

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(15)
                for guild_id, session in list(self._sessions.items()):
                    await self._check_guild_stream(guild_id, session)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("RadioWatchdog loop error: %s", exc, exc_info=True)

    async def _check_guild_stream(self, guild_id: int, session: RadioSession) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            self.unregister(guild_id)
            return

        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return

        player = self.bot.get_player(guild_id)
        if not getattr(player, "is_radio_mode", False):
            self.unregister(guild_id)
            return

        if hasattr(player, "_playing_lock") and player._playing_lock.locked():
            return

        # If voice client is neither playing nor paused, stream has died unexpectedly!
        if not vc.is_playing() and not vc.is_paused():
            music_cog = self.bot.cogs.get("Music")
            if len(player) > 0:
                self.unregister(guild_id)
                if music_cog and hasattr(music_cog, "_play_next"):
                    await music_cog._play_next(guild_id)
                return

            MAX_RECONNECTS = 10
            if session.reconnect_count >= MAX_RECONNECTS:
                logger.warning(
                    "24/7 Radio stream in guild %d ('%s') exceeded max reconnect attempts (%d). Stopping session.",
                    guild_id, session.station_name, MAX_RECONNECTS,
                )
                self.unregister(guild_id)
                return

            session.reconnect_count += 1
            logger.warning(
                "24/7 Radio stream stalled in guild %d ('%s'). Auto-reconnecting attempt #%d...",
                guild_id, session.station_name, session.reconnect_count,
            )

            # Re-enqueue the radio track
            track = Track(
                title=f"📻 {session.station_name}",
                url=session.stream_url,
                duration=0,
                uploader="24/7 Web Radio",
                stream_url_cache=session.stream_url,
            )

            if music_cog and hasattr(music_cog, "_play_next"):
                try:
                    player.is_radio_mode = True
                    await player.enqueue(track)
                    await music_cog._play_next(guild_id)
                    logger.info("Successfully resumed 24/7 radio stream in guild %d.", guild_id)
                except Exception as exc:
                    logger.error("Failed to resume 24/7 radio in guild %d: %s", guild_id, exc)

