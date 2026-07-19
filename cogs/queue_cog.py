# -*- coding: utf-8 -*-
"""cogs/queue_cog.py — Queue management commands for Music Bot V3."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, success_embed, queue_embed
from utils.views import QueueView
from utils.error_handler import dj_required_embed
from utils.color_thief import get_dominant_color

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class QueueCog(commands.Cog, name="Queue"):
    """Queue management commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_dj(self, interaction: discord.Interaction) -> bool:
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True
        member = interaction.user
        if member.guild_permissions.administrator:
            return True
        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True
        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    @app_commands.command(name="queue", description="Show the current queue")
    @app_commands.describe(page="Page number (default: 1)")
    async def queue_cmd(self, interaction: discord.Interaction, page: int = 1) -> None:
        await interaction.response.defer()
        player = self.bot.get_player(interaction.guild_id)
        now    = player.now_playing
        color  = await get_dominant_color(now.thumbnail if now else None, self.bot.http_session)
        embed  = queue_embed(player, page, color=color)
        view   = QueueView(self.bot, interaction.guild_id, page)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        if len(player) < 2:
            await interaction.followup.send(
                embed=error_embed("Not Enough Tracks", "Need at least 2 tracks to shuffle."), ephemeral=True
            )
            return
        await player.shuffle()
        await interaction.followup.send(embed=success_embed("Shuffled 🔀", f"Shuffled {len(player)} tracks."))

    @app_commands.command(name="clear", description="Clear the entire queue")
    async def clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        count  = await player.clear()
        await self.bot.db.clear_queue(interaction.guild_id)
        await interaction.followup.send(embed=success_embed("Queue Cleared", f"Removed {count} tracks."))

    @app_commands.command(name="loop", description="Cycle loop mode: Off → Track → Queue")
    async def loop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player        = self.bot.get_player(interaction.guild_id)
        player.loop_mode = player.loop_mode.next()
        await interaction.followup.send(
            embed=success_embed("Loop Mode", player.loop_mode.label()), ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove a track by position")
    @app_commands.describe(position="1-based position in the queue")
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player  = self.bot.get_player(interaction.guild_id)
        removed = await player.remove(position - 1)
        if removed:
            await interaction.followup.send(
                embed=success_embed("Removed", f"Removed **{removed.short_title}** from position {position}."),
                ephemeral=True,
            )
            vc = interaction.guild.voice_client
            if vc:
                import asyncio
                asyncio.create_task(
                    self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
                )
        else:
            await interaction.followup.send(
                embed=error_embed("Invalid Position", f"No track at position {position}."), ephemeral=True
            )

    @app_commands.command(name="move", description="Move a track to a new position")
    @app_commands.describe(
        from_pos="Current position (1-based)",
        to_pos  ="Target position (1-based)",
    )
    async def move(self, interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player  = self.bot.get_player(interaction.guild_id)
        success = await player.move(from_pos - 1, to_pos - 1)
        if success:
            await interaction.followup.send(
                embed=success_embed("Moved", f"Track moved from position {from_pos} → {to_pos}."),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Invalid Position", "Check both positions are within queue range."), ephemeral=True
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(QueueCog(bot))
