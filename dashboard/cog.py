# -*- coding: utf-8 -*-
"""
dashboard/cog.py — Discord Slash command for the Local Web Dashboard.

Provides:
  - /dashboard: Get direct link and info for the local interactive web player and queue manager
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import info_embed

if TYPE_CHECKING:
    from main import MusicBot


class DashboardCog(commands.Cog, name="Dashboard"):
    """Local Web Dashboard slash command."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="dashboard",
        description="Open the local web player and drag-and-drop queue dashboard",
    )
    async def dashboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        port = config.WEB_PORT
        host = config.WEB_HOST if config.WEB_HOST not in ("0.0.0.0", "") else "localhost"
        url = f"http://{host}:{port}"

        player = self.bot.get_player(interaction.guild_id)
        now = player.now_playing

        status_text = (
            f"▶ **Now Playing:** {now.short_title}\n"
            if now
            else "⏸ **Status:** Idle\n"
        )
        queue_text = f"📋 **Queue:** {len(player)} track(s)"

        embed = info_embed(
            title="🎵 Local Web Dashboard",
            description=(
                f"Control playback and manage the queue directly in your browser:\n\n"
                f"🔗 **[Open Local Dashboard]({url})** (`{url}`)\n\n"
                f"{status_text}"
                f"{queue_text}\n\n"
                f"**Features:**\n"
                f"• ⏯️ **Interactive Web Player:** Play, Pause, Skip, Seekbar, Volume (0-200%)\n"
                f"• 🔀 **Drag-and-Drop Queue:** Easily reorder songs in real-time\n"
                f"• ⚡ **Local WebSocket:** Live updates without external network dependencies"
            ),
        )

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Open Dashboard",
                url=url,
                style=discord.ButtonStyle.link,
                emoji="🌐",
            )
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(DashboardCog(bot))
