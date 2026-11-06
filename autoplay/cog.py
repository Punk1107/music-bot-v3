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

from core.i18n import get_locale, t
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

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if mode == "status":
            state = "✅ Enabled" if player.smart_autoplay else "❌ Disabled"
            await interaction.followup.send(
                embed=info_embed(
                    "📻 Smart Autoplay",
                    f"{t('autoplay.status', locale, status=state)}\n\n{t('autoplay.status_desc', locale)}",
                ),
                ephemeral=True,
            )
            return

        is_on = mode == "on"

        # Permission check: DJ role / administrator / same voice channel
        if interaction.guild and isinstance(interaction.user, discord.Member):
            vc = interaction.guild.voice_client
            if vc and vc.channel and (not interaction.user.voice or interaction.user.voice.channel != vc.channel):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.followup.send(
                        embed=error_embed(t("title.wrong_channel", locale), t("error.wrong_channel", locale, channel=vc.channel.name)),
                        ephemeral=True,
                    )
                    return
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
            if cfg and cfg.dj_only and cfg.dj_role_id and not interaction.user.guild_permissions.administrator:
                dj_role = interaction.guild.get_role(cfg.dj_role_id)
                if dj_role and dj_role not in interaction.user.roles:
                    await interaction.followup.send(
                        embed=error_embed(t("title.permission_denied", locale), t("error.dj_only", locale, role=dj_role.name)),
                        ephemeral=True,
                    )
                    return

        # Toggle on the live player (no DB persistence needed — per-session)
        player.smart_autoplay = is_on

        # Reset history when turning off so fresh picks on next enable
        if not is_on:
            await self.bot.autoplay.reset_history(interaction.guild_id)

        if hasattr(self.bot, "update_clean_board"):
            self.bot.track_task(self.bot.update_clean_board(interaction.guild_id))

        desc = t("autoplay.desc_on", locale) if is_on else t("autoplay.desc_off", locale)
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
