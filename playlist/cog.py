# -*- coding: utf-8 -*-
"""playlist/cog.py — Discord Slash Commands for Multi-Playlist Personal Manager."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from playlist.service import PlaylistService
from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot


class PlaylistCog(commands.Cog, name="Playlist"):
    """Multi-Playlist Personal Manager powered by MySQL 8.0+ relational tables."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self.service = PlaylistService(bot)

    playlist_group = app_commands.Group(name="playlist", description="Multi-Playlist Personal Manager")

    @playlist_group.command(name="create", description="Create a new personal or shared playlist")
    @app_commands.describe(
        name="Unique playlist name",
        description="Optional description for the playlist",
        from_queue="Import currently active server queue tracks into this playlist (default: False)",
        is_public="Make playlist publicly accessible to guild members (default: False)",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        description: Optional[str] = None,
        from_queue: bool = False,
        is_public: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        tracks = []
        if from_queue:
            player = self.bot.get_player(guild_id)
            if player.now_playing:
                tracks.append(player.now_playing)
            tracks.extend(player.queue)

        ok = await self.bot.db.create_playlist(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
            tracks=tracks,
            description=description,
            scope="guild" if is_public else "personal",
            is_public=is_public,
        )

        if ok:
            embed = success_embed(
                "Playlist Created",
                t("playlist.created", locale, name=name, count=len(tracks)),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Creation Failed",
                    "A playlist with that name already exists, or your limit was reached.",
                ),
                ephemeral=True,
            )

    @playlist_group.command(name="add", description="Add a track to your playlist")
    @app_commands.describe(
        name="Playlist name",
        query="Song title, YouTube URL, or 'current' to save currently playing track",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        query: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        track = None
        if query.strip().lower() == "current":
            player = self.bot.get_player(guild_id)
            track = player.now_playing
            if not track:
                await interaction.followup.send(
                    embed=error_embed("Nothing Playing", t("error.not_playing", locale)),
                    ephemeral=True,
                )
                return
        else:
            if hasattr(self.bot, "youtube"):
                if hasattr(self.bot.youtube, "is_youtube_url") and self.bot.youtube.is_youtube_url(query):
                    if hasattr(self.bot, "yt_breaker") and self.bot.yt_breaker:
                        track = await self.bot.yt_breaker.call(self.bot.youtube.get_track, query)
                    else:
                        track = await self.bot.youtube.get_track(query)
                elif hasattr(self.bot.youtube, "search"):
                    if hasattr(self.bot, "yt_breaker") and self.bot.yt_breaker:
                        results = await self.bot.yt_breaker.call(self.bot.youtube.search, query, 1)
                    else:
                        results = await self.bot.youtube.search(query, 1)
                    track = results[0] if results else None
                elif hasattr(self.bot.youtube, "extract_tracks"):
                    tracks, _ = await self.bot.youtube.extract_tracks(query, interaction.user)
                    track = tracks[0] if tracks else None
            if not track:
                await interaction.followup.send(
                    embed=error_embed("Track Not Found", "Could not resolve audio for that query."),
                    ephemeral=True,
                )
                return

        ok = await self.bot.db.add_track_to_playlist(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
            track=track,
        )

        if ok:
            embed = success_embed(
                "Track Added",
                t("playlist.track_added", locale, title=track.title, playlist=name),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Add Failed", f"Playlist `{name}` was not found in your library."),
                ephemeral=True,
            )

    @playlist_group.command(name="remove", description="Remove a track by position from your playlist")
    @app_commands.describe(
        name="Playlist name",
        position="Position of the track in playlist (1-based)",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        name: str,
        position: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        ok = await self.bot.db.remove_track_from_playlist(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
            position=position,
        )

        if ok:
            embed = success_embed(
                "Track Removed",
                t("playlist.track_removed", locale, pos=position, playlist=name),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Remove Failed", "Invalid position or playlist not found."),
                ephemeral=True,
            )

    @playlist_group.command(name="play", description="Load and play tracks from a playlist")
    @app_commands.describe(
        name="Playlist name",
        shuffle="Shuffle tracks before queueing (default: False)",
    )
    async def play(
        self,
        interaction: discord.Interaction,
        name: str,
        shuffle: bool = False,
    ) -> None:
        await interaction.response.defer()
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        member = interaction.user
        if not getattr(member, "voice", None) or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.not_in_voice", locale), t("error.not_in_voice", locale)),
                ephemeral=True,
            )
            return

        tracks = await self.bot.db.load_playlist(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
            increment_play_count=True,
        )

        if not tracks:
            await interaction.followup.send(
                embed=error_embed("Playlist Not Found", t("playlist.not_found", locale, name=name)),
                ephemeral=True,
            )
            return

        channel = member.voice.channel
        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if not vc or not vc.is_connected():
            vc = await channel.connect(self_deaf=True)
        elif vc.channel and vc.channel != channel:
            await interaction.followup.send(
                embed=error_embed(t("title.wrong_channel", locale), t("error.wrong_channel", locale, channel=vc.channel.name)),
                ephemeral=True,
            )
            return

        if shuffle:
            random.shuffle(tracks)

        # Stamp requester
        for t_obj in tracks:
            t_obj.requested_by_id = interaction.user.id
            t_obj.requested_by_name = interaction.user.display_name

        player = self.bot.get_player(guild_id)
        player.last_channel_id = channel.id
        if isinstance(interaction.channel, discord.TextChannel):
            player.text_channel = interaction.channel
        was_empty = (len(player) == 0 and player.now_playing is None)
        await player.extend(tracks)
        if getattr(player, "queue_mode", "fifo") == "fair":
            await player.reorder_fair_share()
        if hasattr(self.bot, "db") and hasattr(self.bot.db, "save_queue"):
            self.bot.track_task(self.bot.db.save_queue(guild_id, channel.id, player.queue))

        # Trigger playback if idle
        if was_empty or (not vc.is_playing() and not vc.is_paused()):
            music_cog = self.bot.cogs.get("Music")
            if music_cog:
                self.bot.track_task(music_cog._play_next(guild_id))
        elif hasattr(self.bot, "update_clean_board"):
            self.bot.track_task(self.bot.update_clean_board(guild_id))

        embed = success_embed(
            "Playlist Queued",
            t("playlist.loaded", locale, count=len(tracks), playlist=name),
        )
        await interaction.followup.send(embed=embed)

    @playlist_group.command(name="share", description="Toggle or set public visibility of your playlist")
    @app_commands.describe(
        name="Playlist name",
        is_public="Whether playlist can be played by all guild members (default: True)",
    )
    async def share(
        self,
        interaction: discord.Interaction,
        name: str,
        is_public: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        ok = await self.bot.db.set_playlist_public(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
            is_public=is_public,
        )

        if ok:
            status = "Public (Shared with Guild)" if is_public else "Private (Personal Only)"
            embed = success_embed(
                "Visibility Updated",
                t("playlist.shared", locale, name=name, status=status),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Update Failed", "Playlist not found or you are not the owner."),
                ephemeral=True,
            )

    @playlist_group.command(name="list", description="List your personal and shared guild playlists")
    @app_commands.describe(include_public="Include public playlists shared by other guild members (default: True)")
    async def list_cmd(
        self,
        interaction: discord.Interaction,
        include_public: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        playlists = await self.bot.db.list_playlists(
            user_id=interaction.user.id,
            guild_id=guild_id,
            include_public=include_public,
        )

        embed = self.service.build_playlist_list_embed(playlists, interaction.user)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist_group.command(name="delete", description="Permanently delete a playlist from your library")
    @app_commands.describe(name="Playlist name to delete")
    async def delete(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)
        ok = await self.bot.db.delete_playlist(
            user_id=interaction.user.id,
            guild_id=guild_id,
            name=name,
        )

        if ok:
            embed = success_embed(
                "Playlist Deleted",
                t("playlist.deleted", locale, name=name),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Delete Failed", "Playlist not found or you are not the owner."),
                ephemeral=True,
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(PlaylistCog(bot))

