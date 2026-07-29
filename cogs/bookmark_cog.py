# -*- coding: utf-8 -*-
"""cogs/bookmark_cog.py — Queue Bookmark commands for Music Bot V3.

Tier-S Feature 5: Queue Bookmark (Snapshot)
  - /bookmark save <name>   — snapshot current queue
  - /bookmark load <name>   — restore queue (replace or append)
  - /bookmark list          — show all bookmarks
  - /bookmark delete <name> — remove a bookmark

Bookmarks are per-user per-guild snapshots of the queue. Unlike Favorites
(single tracks), a Bookmark captures the entire queue state at a point in time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import (
    error_embed, success_embed, info_embed,
    bookmark_list_embed, bookmark_saved_embed, bookmark_loaded_embed,
)

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class BookmarkCog(commands.Cog, name="Bookmark"):
    """Queue snapshot (bookmark) commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── /bookmark save ────────────────────────────────────────────────────────

    @app_commands.command(name="bookmark", description="Manage queue bookmarks (snapshots)")
    @app_commands.describe(action="save / load / list / delete")
    @app_commands.choices(action=[
        app_commands.Choice(name="💾 Save — snapshot current queue",   value="save"),
        app_commands.Choice(name="📂 Load — restore a bookmark",       value="load"),
        app_commands.Choice(name="📋 List — show all bookmarks",       value="list"),
        app_commands.Choice(name="🗑 Delete — remove a bookmark",      value="delete"),
    ])
    async def bookmark(
        self,
        interaction: discord.Interaction,
        action: str,
        name: str = "",
        mode: str = "append",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if action == "save":
            await self._save(interaction, name)
        elif action == "load":
            await self._load(interaction, name, mode)
        elif action == "list":
            await self._list(interaction)
        elif action == "delete":
            await self._delete(interaction, name)
        else:
            await interaction.followup.send(
                embed=error_embed("Unknown Action", "Choose: save, load, list, delete"), ephemeral=True
            )

    # ── Sub-commands (also exposed individually for slash autocomplete) ────────

    @app_commands.command(name="bsave", description="Save the current queue as a named bookmark")
    @app_commands.describe(name="Bookmark name (max 50 chars)")
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

    async def _save(self, interaction: discord.Interaction, name: str) -> None:
        name = name.strip()[:50]
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", "Provide a name for the bookmark (e.g. `/bsave Study Playlist`)."),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        queue  = player.queue

        if not queue and not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Empty Queue", "There are no tracks to bookmark."), ephemeral=True
            )
            return

        # Include now_playing at front if active
        snapshot = []
        if player.now_playing:
            snapshot.append(player.now_playing)
        snapshot.extend(queue)

        ok = await self.bot.db.save_bookmark(
            interaction.user.id, interaction.guild_id, name, snapshot
        )
        if ok:
            await interaction.followup.send(
                embed=bookmark_saved_embed(name, len(snapshot)), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Save Failed",
                    "A bookmark with that name already exists, or you've reached the 20-bookmark limit.\n"
                    "Use `/bdelete <name>` to remove an old bookmark first.",
                ),
                ephemeral=True,
            )

    async def _load(self, interaction: discord.Interaction, name: str, mode: str) -> None:
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", "Provide the bookmark name to load."), ephemeral=True
            )
            return

        tracks = await self.bot.db.load_bookmark(interaction.user.id, interaction.guild_id, name)
        if tracks is None:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"No bookmark named **`{name}`**. Use `/blist` to see yours."),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        vc     = interaction.guild.voice_client

        if mode == "replace":
            await player.clear()

        for t in tracks:
            t.requested_by_id   = interaction.user.id
            t.requested_by_name = interaction.user.display_name

        await player.extend(tracks)

        if vc:
            asyncio.create_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )

        await interaction.followup.send(
            embed=bookmark_loaded_embed(name, len(tracks), mode), ephemeral=True
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
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("Name Required", "Provide the bookmark name to delete."), ephemeral=True
            )
            return

        ok = await self.bot.db.delete_bookmark(interaction.user.id, interaction.guild_id, name)
        if ok:
            await interaction.followup.send(
                embed=success_embed("Bookmark Deleted 🗑", f"Deleted bookmark **`{name}`**."), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"No bookmark named **`{name}`**."), ephemeral=True
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(BookmarkCog(bot))
