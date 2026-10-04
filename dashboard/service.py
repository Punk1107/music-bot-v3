# -*- coding: utf-8 -*-
"""
dashboard/service.py — Core service for the Local Web Dashboard.

Handles:
  - Playback control: Pause, Resume, Skip
  - Volume control with instant PCMVolumeTransformer update
  - Seamless seeking via SeekService
  - Drag-and-drop queue reordering and track removal
  - State serialization and real-time broadcasting
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

import discord

if TYPE_CHECKING:
    from main import MusicBot
    from core.player import GuildPlayer

logger = logging.getLogger(__name__)


class DashboardService:
    """Service providing unified player control and state access for web dashboard."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self._listeners: set[Callable[[int, dict], Coroutine[Any, Any, None]]] = set()

    def register_listener(
        self, callback: Callable[[int, dict], Coroutine[Any, Any, None]]
    ) -> None:
        """Register a callback for guild state updates: callback(guild_id, state)."""
        self._listeners.add(callback)

    def unregister_listener(
        self, callback: Callable[[int, dict], Coroutine[Any, Any, None]]
    ) -> None:
        """Unregister a previously registered callback."""
        self._listeners.discard(callback)

    async def broadcast_state(self, guild_id: int) -> None:
        """Fetch current state and notify all registered listeners."""
        state = self.get_guild_state(guild_id)
        dead: set[Callable] = set()
        for cb in list(self._listeners):
            try:
                await cb(guild_id, state)
            except Exception as exc:
                logger.debug("Dashboard listener error: %s", exc)
                dead.add(cb)
        self._listeners -= dead

    # ── State Serialization ───────────────────────────────────────────────────

    def get_all_guilds(self) -> list[dict]:
        """Return list of all guilds the bot is currently in."""
        result = []
        for guild in self.bot.guilds:
            player = self.bot.get_player(guild.id)
            vc: Optional[discord.VoiceClient] = guild.voice_client
            result.append(
                {
                    "id": str(guild.id),
                    "name": guild.name,
                    "icon": str(guild.icon.url) if guild.icon else None,
                    "voice_connected": bool(vc and vc.is_connected()),
                    "voice_channel": vc.channel.name if (vc and vc.channel) else None,
                    "active": bool(player.now_playing),
                    "queue_size": len(player),
                }
            )
        return result

    def get_guild_state(self, guild_id: int) -> dict:
        """Return a complete state snapshot for a single guild."""
        guild = self.bot.get_guild(guild_id)
        player = self.bot.get_player(guild_id)
        vc: Optional[discord.VoiceClient] = guild.voice_client if guild else None

        now_playing_data = None
        if player.now_playing:
            t = player.now_playing
            now_playing_data = {
                "title": t.title,
                "url": t.url,
                "duration": t.duration,
                "thumbnail": t.thumbnail,
                "uploader": t.uploader,
                "requested_by": t.requested_by_name or (f"User#{t.requested_by_id}" if t.requested_by_id else None),
                "elapsed": player.elapsed_seconds,
                "remaining": player.remaining_seconds,
                "progress": round(player.progress_fraction() * 100, 1),
            }

        queue_items = []
        for i, track in enumerate(player.queue):
            queue_items.append(
                {
                    "index": i,
                    "title": track.title,
                    "url": track.url,
                    "duration": track.duration,
                    "thumbnail": track.thumbnail,
                    "uploader": track.uploader,
                    "requested_by": track.requested_by_name or (f"User#{track.requested_by_id}" if track.requested_by_id else None),
                    "eta_seconds": player.eta_seconds(i + 1),
                }
            )

        total_dur = sum(t.duration for t in player.queue if t.duration)
        if player.now_playing and player.now_playing.duration:
            total_dur += player.remaining_seconds

        return {
            "guild_id": str(guild_id),
            "guild_name": guild.name if guild else str(guild_id),
            "voice_connected": bool(vc and vc.is_connected()),
            "voice_channel": vc.channel.name if (vc and vc.channel) else None,
            "is_playing": bool(vc and vc.is_playing()),
            "is_paused": bool(vc and vc.is_paused()),
            "now_playing": now_playing_data,
            "queue": queue_items,
            "queue_size": len(player),
            "volume": round(player.volume, 2),
            "volume_percent": int(round(player.volume * 100)),
            "loop_mode": player.loop_mode.value,
            "effects": [e.value for e in player.effects] if hasattr(player, "effects") else [],
            "total_duration": total_dur,
        }

    # ── Playback Controls ─────────────────────────────────────────────────────

    async def pause_or_resume(
        self, guild_id: int, action: Optional[str] = None
    ) -> dict:
        """
        Toggle or explicitly set pause/resume for a guild.
        action: 'pause', 'resume', or None/'toggle'
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"success": False, "error": f"Guild {guild_id} not found"}

        vc: Optional[discord.VoiceClient] = guild.voice_client
        if not vc or not vc.is_connected():
            return {"success": False, "error": "Bot is not connected to a voice channel"}

        if action == "pause":
            if vc.is_playing():
                vc.pause()
            paused = True
        elif action == "resume":
            if vc.is_paused():
                vc.resume()
            paused = False
        else:  # toggle
            if vc.is_paused():
                vc.resume()
                paused = False
            elif vc.is_playing():
                vc.pause()
                paused = True
            else:
                return {"success": False, "error": "No active audio playing"}

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "paused": paused,
            "is_playing": vc.is_playing(),
            "is_paused": vc.is_paused(),
        }

    async def skip(self, guild_id: int) -> dict:
        """Skip the currently playing track."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"success": False, "error": f"Guild {guild_id} not found"}

        vc: Optional[discord.VoiceClient] = guild.voice_client
        player = self.bot.get_player(guild_id)

        if not player.now_playing or not (vc and (vc.is_playing() or vc.is_paused())):
            return {"success": False, "error": "Nothing currently playing to skip"}

        skipped_title = player.now_playing.title
        player.cancel_prefetch()
        vc.stop()

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "message": f"Skipped: {skipped_title}",
            "skipped_track": skipped_title,
        }

    async def set_volume(self, guild_id: int, volume: float) -> dict:
        """
        Adjust volume (0-200% or 0.0-2.0).
        Applies immediately to PCMVolumeTransformer if available.
        """
        player = self.bot.get_player(guild_id)

        # Allow 0-200 or 0.0-2.0
        if volume > 2.0:
            volume = volume / 100.0
        volume = max(0.0, min(2.0, volume))

        player.volume = volume

        # Live volume adjustment on active source without FFmpeg reload
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and guild.voice_client.source:
            src = guild.voice_client.source
            if hasattr(src, "volume"):
                src.volume = volume

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "volume": round(player.volume, 2),
            "volume_percent": int(round(player.volume * 100)),
        }

    async def seek(self, guild_id: int, target_seconds: int) -> dict:
        """Seek playback to target_seconds."""
        player = self.bot.get_player(guild_id)
        if not player.now_playing:
            return {"success": False, "error": "No track currently playing"}

        # Use SeekService if available
        seek_svc = getattr(self.bot, "seek", None)
        if not seek_svc:
            from seek.service import SeekService
            seek_svc = SeekService(self.bot)

        ok = await seek_svc.seek_to(guild_id, target_seconds)
        if ok:
            await self.broadcast_state(guild_id)
            return {"success": True, "position": target_seconds}
        return {"success": False, "error": "Seek operation failed"}

    # ── Queue Controls ────────────────────────────────────────────────────────

    async def move_queue(
        self, guild_id: int, from_index: int, to_index: int
    ) -> dict:
        """
        Move a track in the queue from from_index to to_index (0-based).
        Used by HTML5 Drag and Drop API.
        """
        player = self.bot.get_player(guild_id)
        q_len = len(player)

        if not (0 <= from_index < q_len and 0 <= to_index < q_len):
            return {
                "success": False,
                "error": f"Index out of range (from={from_index}, to={to_index}, len={q_len})",
            }

        if from_index == to_index:
            return {"success": True, "message": "Position unchanged"}

        player.undo_push("move", extra=(from_index, to_index))
        ok = await player.move(from_index, to_index)

        if not ok:
            player.undo_pop()
            return {"success": False, "error": "Failed to reorder queue"}

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "from_index": from_index,
            "to_index": to_index,
            "queue_size": len(player),
        }

    async def remove_from_queue(self, guild_id: int, index: int) -> dict:
        """Remove a track from the queue at index (0-based)."""
        player = self.bot.get_player(guild_id)
        q_len = len(player)

        if not (0 <= index < q_len):
            return {"success": False, "error": f"Index {index} out of range (len={q_len})"}

        removed = await player.remove(index)
        if removed:
            player.undo_push("remove", extra=(index, removed))
            await self.broadcast_state(guild_id)
            return {"success": True, "removed": removed.to_dict()}
        return {"success": False, "error": "Failed to remove track"}

    async def clear_queue(self, guild_id: int) -> dict:
        """Clear the entire queue."""
        player = self.bot.get_player(guild_id)
        count = await player.clear()
        await self.broadcast_state(guild_id)
        return {"success": True, "cleared_count": count}

    async def shuffle_queue(self, guild_id: int) -> dict:
        """Shuffle the queue."""
        player = self.bot.get_player(guild_id)
        if len(player) < 2:
            return {"success": False, "error": "Not enough tracks to shuffle"}

        player.undo_push("shuffle")
        await player.shuffle()
        await self.broadcast_state(guild_id)
        return {"success": True, "queue_size": len(player)}

    # ── Advanced Controls (Loop, Effects, Search, Add, Lyrics) ────────────────

    async def set_loop_mode(self, guild_id: int, mode: str) -> dict:
        """Set loop mode ('off', 'track', 'queue')."""
        from models.enums import LoopMode
        mode_str = str(mode).lower().strip()
        if mode_str not in ("off", "track", "queue"):
            return {"success": False, "error": f"Invalid loop mode: {mode}"}
        player = self.bot.get_player(guild_id)
        player.loop_mode = LoopMode(mode_str)
        await self.broadcast_state(guild_id)
        return {"success": True, "loop_mode": player.loop_mode.value}

    async def toggle_effect(self, guild_id: int, effect_name: str) -> dict:
        """Toggle an audio effect on/off for the given guild."""
        from models.enums import AudioEffect
        eff_enum = None
        target = effect_name.lower().strip()
        for ae in AudioEffect:
            if ae.value.lower() == target or ae.name.lower() == target:
                eff_enum = ae
                break
        if not eff_enum:
            return {"success": False, "error": f"Unknown effect: {effect_name}"}

        player = self.bot.get_player(guild_id)
        if eff_enum in player.effects:
            player.effects.remove(eff_enum)
            enabled = False
        else:
            player.effects.append(eff_enum)
            enabled = True

        # Hot-reload audio filter if playing
        seek_svc = getattr(self.bot, "seek", None)
        if not seek_svc:
            try:
                from seek.service import SeekService
                seek_svc = SeekService(self.bot)
            except Exception:
                seek_svc = None
        if seek_svc and hasattr(seek_svc, "hot_reload"):
            asyncio.create_task(seek_svc.hot_reload(guild_id))

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "effect": eff_enum.value,
            "enabled": enabled,
            "effects": [e.value for e in player.effects],
        }

    async def search_tracks(self, query: str, limit: int = 5) -> list[dict]:
        """Search tracks via YouTube or resolve URL."""
        query = query.strip()
        if not query:
            return []
        try:
            yt = getattr(self.bot, "youtube", None)
            if yt:
                if yt.is_youtube_url(query):
                    track = await yt.get_track(query)
                    return [track.to_dict()] if track else []
                tracks = await yt.search(query, limit=limit)
                return [t.to_dict() for t in tracks]
        except Exception as exc:
            logger.error("Dashboard search error: %s", exc)
        return []

    async def add_to_queue(
        self,
        guild_id: int,
        query: str,
        play_next: bool = False,
        requested_by: str = "Web Dashboard",
    ) -> dict:
        """Resolve a track query or URL and add to guild queue."""
        query = query.strip()
        if not query:
            return {"success": False, "error": "Query cannot be empty"}

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"success": False, "error": f"Guild {guild_id} not found"}

        player = self.bot.get_player(guild_id)
        yt = getattr(self.bot, "youtube", None)
        sp = getattr(self.bot, "spotify", None)
        tracks = []

        try:
            if sp and sp.is_spotify_url(query):
                import config
                tracks = await sp.resolve(
                    query, self.bot.http_session, yt, config.MAX_PLAYLIST_TRACKS
                )
            elif yt and yt.is_playlist_url(query):
                import config
                tracks = await yt.get_playlist(query, config.MAX_PLAYLIST_TRACKS)
            elif yt and yt.is_youtube_url(query):
                track = await yt.get_track(query)
                if track:
                    tracks = [track]
            elif yt:
                found = await yt.search(query, limit=1)
                if found:
                    tracks = found
        except Exception as exc:
            logger.error("Dashboard enqueue error resolving query: %s", exc)
            return {"success": False, "error": f"Resolution failed: {exc}"}

        if not tracks:
            return {"success": False, "error": "No tracks found"}

        for t in tracks:
            t.requested_by_name = requested_by

        if play_next:
            if len(tracks) == 1:
                await player.enqueue_next(tracks[0])
            else:
                await player.extend_next(tracks)
        else:
            if len(tracks) == 1:
                await player.enqueue(tracks[0])
            else:
                await player.extend(tracks)

        # Trigger playback if idle and voice connected
        vc = guild.voice_client
        if vc and not vc.is_playing() and not vc.is_paused():
            music_cog = self.bot.get_cog("Music")
            if music_cog and hasattr(music_cog, "_play_next"):
                asyncio.create_task(music_cog._play_next(guild_id))

        await self.broadcast_state(guild_id)
        return {
            "success": True,
            "added_count": len(tracks),
            "tracks": [t.to_dict() for t in tracks],
            "play_next": play_next,
        }

    async def get_lyrics(self, guild_id: int) -> dict:
        """Fetch synced lyrics for currently playing track."""
        player = self.bot.get_player(guild_id)
        if not player.now_playing:
            return {"success": False, "error": "No track currently playing"}

        track = player.now_playing
        try:
            from lyrics.service import LyricsService
            lyrics_svc = LyricsService()
            result = await lyrics_svc.get_lyrics(track.url, self.bot.http_session)
            if not result:
                return {
                    "success": False,
                    "error": "No lyrics found for this track",
                    "title": track.title,
                }
            lines, is_auto = result
            if not lines:
                return {
                    "success": False,
                    "error": "No lyrics found for this track",
                    "title": track.title,
                }
            return {
                "success": True,
                "title": track.title,
                "uploader": track.uploader,
                "is_auto": is_auto,
                "lines": [
                    {
                        "start": line.start_sec,
                        "timestamp": line.timestamp_str,
                        "text": line.text,
                    }
                    for line in lines
                ],
            }
        except Exception as exc:
            logger.error("Dashboard get_lyrics error: %s", exc)
            return {"success": False, "error": str(exc), "title": track.title}
