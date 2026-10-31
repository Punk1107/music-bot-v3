# -*- coding: utf-8 -*-
"""cogs/bookmark_cog.py — Multi-Playlist & Queue Bookmark commands for Music Bot V3 (Gen 5.0).

Tier-S Feature 5 & Gen 5.0 Multi-Playlist Management:
  - /bookmark save <name> [scope] [public] — snapshot current queue into `user_playlists` & `playlist_tracks`
  - /bookmark load <name>                  — restore playlist/bookmark into queue (replace or append)
  - /bookmark list                         — show all personal & public guild/global playlists
  - /bookmark delete <name>                — remove a playlist/bookmark (cascades to playlist_tracks)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import (
    error_embed, success_embed, info_embed,
    bookmark_list_embed, bookmark_saved_embed, bookmark_loaded_embed,
)
from core.i18n import get_locale, t

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class BookmarkCog(commands.Cog, name="Bookmark"):
    """Queue snapshot (bookmark) & Gen 5.0 Multi-Playlist commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── Slash Command Group: /bookmark ────────────────────────────────────────

    bookmark_group = app_commands.Group(
        name="bookmark",
        description="Manage queue bookmarks & multi-playlists (Gen 5.0)",
    )

    @bookmark_group.command(name="save", description="Save the current queue as a named playlist/bookmark")
    @app_commands.describe(
        name="Playlist/Bookmark name (max 100 chars)",
        scope="Visibility scope: personal, guild, or global",
        is_public="Allow other members to load this playlist",
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="Personal (Only you)", value="personal"),
        app_commands.Choice(name="Guild (This server)", value="guild"),
        app_commands.Choice(name="Global (All servers)", value="global"),
    ])
    async def bm_save(
        self,
        interaction: discord.Interaction,
        name: str,
        scope: str = "personal",
        is_public: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._save(interaction, name, scope=scope, is_public=is_public)

    @bookmark_group.command(name="load", description="Load a bookmark/playlist into the queue")
    @app_commands.describe(
        name="Bookmark/Playlist name to load",
        mode="replace = clear queue first, append = add to queue",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Replace queue", value="replace"),
        app_commands.Choice(name="Append to queue", value="append"),
    ])
    async def bm_load(self, interaction: discord.Interaction, name: str, mode: str = "append") -> None:
        await interaction.response.defer(ephemeral=True)
        await self._load(interaction, name, mode)

    @bookmark_group.command(name="list", description="List all your queue bookmarks & public playlists")
    async def bm_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._list(interaction)

    @bookmark_group.command(name="delete", description="Delete a queue bookmark/playlist")
    @app_commands.describe(name="Bookmark name to delete")
    async def bm_delete(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._delete(interaction, name)

    # ── Shortcuts (/bsave, /bload, /blist, /bdelete) ───────────────────────────

    @app_commands.command(name="bsave", description="Save the current queue as a named bookmark")
    @app_commands.describe(name="Bookmark name (max 100 chars)")
    async def bsave(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._save(interaction, name)

    @app_commands.command(name="bload", description="Load a bookmark into the queue")
    @app_commands.describe(
        name="Bookmark name to load",
        mode="replace = clear queue first, append = add to queue",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Replace queue", value="replace"),
        app_commands.Choice(name="Append to queue", value="append"),
    ])
    async def bload(self, interaction: discord.Interaction, name: str, mode: str = "append") -> None:
        await interaction.response.defer(ephemeral=True)
        await self._load(interaction, name, mode)

    @app_commands.command(name="blist", description="List all your queue bookmarks")
    async def blist(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._list(interaction)

    @app_commands.command(name="bdelete", description="Delete a queue bookmark")
    @app_commands.describe(name="Bookmark name to delete")
    async def bdelete(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._delete(interaction, name)

    # ── Autocomplete ──────────────────────────────────────────────────────────

    @bm_load.autocomplete("name")
    @bm_delete.autocomplete("name")
    @bload.autocomplete("name")
    @bdelete.autocomplete("name")
    async def bookmark_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        bookmarks = await self.bot.db.list_bookmarks(interaction.user.id, interaction.guild_id)
        return [
            app_commands.Choice(name=f"{bm['name']} ({bm['count']} tracks)", value=bm["name"])
            for bm in bookmarks
            if current.lower() in bm["name"].lower()
        ][:25]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _save(
        self,
        interaction: discord.Interaction,
        name: str,
        *,
        scope: str = "personal",
        is_public: bool = False,
    ) -> None:
        locale = await get_locale(interaction.guild_id, self.bot.db)
        name = name.strip()[:100]
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", t("bookmark.name_required", locale)),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        queue  = player.queue

        if not queue and not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Empty Queue", t("bookmark.empty_queue", locale)), ephemeral=True
            )
            return

        snapshot = []
        if player.now_playing:
            snapshot.append(player.now_playing)
        snapshot.extend(queue)

        if scope in ("guild", "global"):
            is_public = True

        try:
            ok = await self.bot.db.save_bookmark(
                interaction.user.id,
                interaction.guild_id,
                name,
                snapshot,
                scope=scope,
                is_public=is_public,
            )
        except TypeError:
            # Backward-compat with mocks that only accept 4 positional args
            ok = await self.bot.db.save_bookmark(
                interaction.user.id, interaction.guild_id, name, snapshot
            )

        if ok:
            await interaction.followup.send(
                embed=bookmark_saved_embed(name, len(snapshot), locale=locale), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Save Failed",
                    t("bookmark.save_failed", locale),
                ),
                ephemeral=True,
            )

    async def _load(self, interaction: discord.Interaction, name: str, mode: str) -> None:
        locale = await get_locale(interaction.guild_id, self.bot.db)
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", t("bookmark.name_required", locale)), ephemeral=True
            )
            return

        tracks = await self.bot.db.load_bookmark(interaction.user.id, interaction.guild_id, name)
        if tracks is None:
            await interaction.followup.send(
                embed=error_embed("Not Found", t("bookmark.not_found", locale, name=name)),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        vc     = interaction.guild.voice_client

        if mode == "replace":
            await player.clear()

        for t_track in tracks:
            t_track.requested_by_id   = interaction.user.id
            t_track.requested_by_name = interaction.user.display_name

        await player.extend(tracks)

        if vc:
            self.bot.track_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )

        await interaction.followup.send(
            embed=bookmark_loaded_embed(name, len(tracks), mode, locale=locale), ephemeral=True
        )

        # Start playback if bot is in voice and nothing playing
        if vc and not vc.is_playing() and not vc.is_paused():
            music_cog = self.bot.get_cog("Music")
            if music_cog:
                await music_cog._play_next(interaction.guild_id)

    async def _list(self, interaction: discord.Interaction) -> None:
        bookmarks = await self.bot.db.list_bookmarks(interaction.user.id, interaction.guild_id)
        embed = bookmark_list_embed(bookmarks, interaction.user)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _delete(self, interaction: discord.Interaction, name: str) -> None:
        locale = await get_locale(interaction.guild_id, self.bot.db)
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", t("bookmark.name_required", locale)), ephemeral=True
            )
            return

        ok = await self.bot.db.delete_bookmark(interaction.user.id, interaction.guild_id, name)
        if ok:
            await interaction.followup.send(
                embed=success_embed("Bookmark Deleted 🗑", t("bookmark.deleted", locale, name=name)), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Not Found", t("bookmark.not_found", locale, name=name)), ephemeral=True
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(BookmarkCog(bot))
