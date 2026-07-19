# -*- coding: utf-8 -*-
"""
cogs/favorites.py — Favorites system for Music Bot V3 (NEW).

Commands:
  /favorite add [name]      — Save now-playing as favorite
  /favorite list [user]     — View favorites (paginated)
  /favorite play <name>     — Enqueue and play a saved favorite
  /favorite remove <name>   — Delete a saved favorite
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import (
    error_embed, success_embed, info_embed,
    favorites_list_embed, favorite_added_embed, favorite_removed_embed,
)
from utils.views import FavoritesView
from utils.formatters import truncate

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class FavoritesCog(commands.Cog, name="Favorites"):
    """Per-user favorites system."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── Group ─────────────────────────────────────────────────────────────────

    favorites_group = app_commands.Group(
        name        = "favorite",
        description = "Manage your favorite tracks ❤️",
    )

    @favorites_group.command(name="add", description="Save the current track as a favorite")
    @app_commands.describe(name="Custom name (default: track title)")
    async def fav_add(
        self,
        interaction: discord.Interaction,
        name:        str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Nothing Playing", "Play a track first before saving."),
                ephemeral=True,
            )
            return

        track = player.now_playing
        fav_name = (name or track.title)[:100].strip()
        if not fav_name:
            fav_name = track.title[:100]

        count = await self.bot.db.count_favorites(interaction.user.id, interaction.guild_id)
        if count >= config.MAX_FAVORITES_PER_USER:
            await interaction.followup.send(
                embed=error_embed(
                    "Favorites Full",
                    f"You have reached the limit of {config.MAX_FAVORITES_PER_USER} favorites.\n"
                    "Remove some with `/favorite remove`.",
                ),
                ephemeral=True,
            )
            return

        ok = await self.bot.db.add_favorite(interaction.user.id, interaction.guild_id, fav_name, track)
        if ok:
            await interaction.followup.send(
                embed=favorite_added_embed(fav_name, track), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Already Exists",
                    f"You already have a favorite named `{fav_name}`.\nUse `/favorite remove {fav_name}` first.",
                ),
                ephemeral=True,
            )

    @favorites_group.command(name="list", description="View your favorite tracks")
    @app_commands.describe(user="View another user's favorites (default: yours)")
    async def fav_list(
        self,
        interaction: discord.Interaction,
        user:        discord.User | None = None,
    ) -> None:
        await interaction.response.defer()
        target    = user or interaction.user
        favorites = await self.bot.db.get_favorites(target.id, interaction.guild_id)

        if not favorites:
            await interaction.followup.send(
                embed=info_embed(
                    "No Favorites",
                    f"{'You have' if target == interaction.user else f'{target.display_name} has'} "
                    "no saved favorites yet.\n"
                    "*Use `/favorite add` while a track is playing to save it.*",
                ),
                ephemeral=True,
            )
            return

        embed = favorites_list_embed(favorites, target, page=1)
        view  = FavoritesView(self.bot, target, interaction.guild_id, favorites, page=1)
        await interaction.followup.send(embed=embed, view=view)

    @favorites_group.command(name="play", description="Enqueue and play a saved favorite")
    @app_commands.describe(name="Name of the favorite to play")
    async def fav_play(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()

        track = await self.bot.db.get_favorite_by_name(interaction.user.id, interaction.guild_id, name)
        if not track:
            # Try fuzzy match (first partial match)
            favorites = await self.bot.db.get_favorites(interaction.user.id, interaction.guild_id)
            matched = next(
                (f for f in favorites if name.lower() in f["name"].lower()), None
            )
            if matched:
                track = matched["track"]
            else:
                await interaction.followup.send(
                    embed=error_embed("Not Found", f"No favorite named `{name}`. Use `/favorite list` to see yours."),
                    ephemeral=True,
                )
                return

        track.is_favorite = True
        # Delegate to music cog
        music_cog = self.bot.cogs.get("Music")
        if music_cog:
            await music_cog.play_track(interaction, track)
        else:
            await interaction.followup.send(
                embed=error_embed("Error", "Music cog not loaded."), ephemeral=True
            )

    @fav_play.autocomplete("name")
    async def fav_play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        favorites = await self.bot.db.get_favorites(interaction.user.id, interaction.guild_id)
        return [
            app_commands.Choice(name=truncate(f["name"], 100), value=f["name"])
            for f in favorites
            if current.lower() in f["name"].lower()
        ][:25]

    @favorites_group.command(name="remove", description="Delete a saved favorite")
    @app_commands.describe(name="Name of the favorite to remove")
    async def fav_remove(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        removed = await self.bot.db.remove_favorite(interaction.user.id, interaction.guild_id, name)
        if removed:
            await interaction.followup.send(
                embed=favorite_removed_embed(name), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"No favorite named `{name}`."), ephemeral=True
            )

    @fav_remove.autocomplete("name")
    async def fav_remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        favorites = await self.bot.db.get_favorites(interaction.user.id, interaction.guild_id)
        return [
            app_commands.Choice(name=truncate(f["name"], 100), value=f["name"])
            for f in favorites
            if current.lower() in f["name"].lower()
        ][:25]


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(FavoritesCog(bot))
