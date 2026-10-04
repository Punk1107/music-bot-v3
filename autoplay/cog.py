# -*- coding: utf-8 -*-
"""
autoplay/cog.py — Smart Autoplay slash commands for Music Bot V3 (Feature 1.4).

Commands:
  /autoplay on          — Enable Smart Autoplay for this server
  /autoplay off         — Disable Smart Autoplay for this server
  /autoplay status      — Show current Smart Autoplay status
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, success_embed, info_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class AutoplayCog(commands.Cog, name="Autoplay"):
    """Feature 1.4 — Smart Autoplay from YouTube Related Videos."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── /autoplay ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name        = "autoplay",
        description = "Toggle Smart Autoplay (plays related YouTube videos when queue runs out)",
    )
    @app_commands.describe(mode="on / off / status")
    @app_commands.choices(mode=[
        app_commands.Choice(name="On",     value="on"),
        app_commands.Choice(name="Off",    value="off"),
        app_commands.Choice(name="Status", value="status"),
    ])
    async def autoplay(self, interaction: discord.Interaction, mode: str) -> None:
        await interaction.response.defer(ephemeral=True)

        player = self.bot.get_player(interaction.guild_id)

        if mode == "status":
            state = "✅ Enabled" if player.smart_autoplay else "❌ Disabled"
            await interaction.followup.send(
                embed=info_embed(
                    "📻 Smart Autoplay",
                    f"Smart Autoplay is currently **{state}** for this server.\n\n"
                    "When the queue empties, the bot will automatically recommend "
                    "and play related YouTube videos based on the last-played song.",
                ),
                ephemeral=True,
            )
            return

        is_on = mode == "on"

        # Toggle on the live player (no DB persistence needed — per-session)
        player.smart_autoplay = is_on

        # Reset history when turning off so fresh picks on next enable
        if not is_on:
            await self.bot.autoplay.reset_history(interaction.guild_id)

        status_str = "enabled ✅" if is_on else "disabled ❌"
        desc = (
            f"Smart Autoplay has been **{status_str}**.\n\n"
            + (
                "🎵 When the queue runs out, I'll automatically play related YouTube videos "
                "based on the last song.\n"
                "Use `/autoplay off` to stop."
                if is_on else
                "The bot will no longer auto-recommend songs when the queue is empty."
            )
        )
        await interaction.followup.send(
            embed=success_embed("📻 Smart Autoplay", desc),
            ephemeral=True,
        )
        logger.info(
            "Smart Autoplay %s for guild %d by %s",
            "enabled" if is_on else "disabled",
            interaction.guild_id,
            interaction.user,
        )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(AutoplayCog(bot))
