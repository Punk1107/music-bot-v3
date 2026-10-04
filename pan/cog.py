# -*- coding: utf-8 -*-
"""
pan/cog.py — Discord slash commands for Audio Pan & Stereo Soundstage Enhancement (Feature 2.8).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from pan.filter import clamp_balance, clamp_width
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


class PanCog(commands.Cog, name="Pan"):
    """Audio Pan (L-R balance) and Stereo Soundstage Enhancement (Feature 2.8)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Verify the user is connected to voice and has DJ/Admin permissions."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False

        if not member.voice or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed("Not in Voice", "Join a voice channel first."),
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if vc and vc.channel != member.voice.channel:
            await interaction.followup.send(
                embed=error_embed("Wrong Channel", f"Join **{vc.channel.name}** to use controls."),
                ephemeral=True,
            )
            return False

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True
        if member.guild_permissions.administrator:
            return True
        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True

        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    def _restart_audio(self, guild_id: int) -> None:
        """Hot-reload current playback to apply new pan / stereo filters seamlessly."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    @app_commands.command(name="pan", description="Adjust audio Left-Right balance")
    @app_commands.describe(balance="Pan position from full left to full right")
    @app_commands.choices(balance=[
        app_commands.Choice(name="◀️ Full Left (100% L)",   value=-1.0),
        app_commands.Choice(name="◀️ Medium Left (50% L)",  value=-0.5),
        app_commands.Choice(name="⏺️ Center (Balanced)",    value=0.0),
        app_commands.Choice(name="▶️ Medium Right (50% R)", value=0.5),
        app_commands.Choice(name="▶️ Full Right (100% R)",  value=1.0),
    ])
    async def pan(self, interaction: discord.Interaction, balance: float) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        b = clamp_balance(balance)
        player = self.bot.get_player(interaction.guild_id)
        player.pan_balance = b
        self._restart_audio(interaction.guild_id)

        if abs(b) < 0.05:
            pos_label = "Center (0%)"
        elif b < 0:
            pos_label = f"Left {int(abs(b) * 100)}%"
        else:
            pos_label = f"Right {int(b * 100)}%"

        await interaction.followup.send(
            embed=success_embed("Audio Pan", t("pan.set", locale, position=pos_label)),
            ephemeral=True,
        )

    @app_commands.command(name="stereowide", description="Enhance and widen stereo soundstage")
    @app_commands.describe(width="Soundstage width multiplier")
    @app_commands.choices(width=[
        app_commands.Choice(name="🔉 Mono (0.0x)",        value=0.0),
        app_commands.Choice(name="🎧 Narrow (0.5x)",      value=0.5),
        app_commands.Choice(name="🎵 Normal Stereo (1.0x)", value=1.0),
        app_commands.Choice(name="🌌 Wide (1.5x)",        value=1.5),
        app_commands.Choice(name="✨ Ultra-Wide (2.0x)",   value=2.0),
    ])
    async def stereowide(self, interaction: discord.Interaction, width: float) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        w = clamp_width(width)
        player = self.bot.get_player(interaction.guild_id)
        player.stereo_width = w
        self._restart_audio(interaction.guild_id)

        mode_labels = {
            0.0: "Mono (0.0x)",
            0.5: "Narrow (0.5x)",
            1.0: "Normal Stereo (1.0x)",
            1.5: "Wide (1.5x)",
            2.0: "Ultra-Wide (2.0x)",
        }
        label = mode_labels.get(w, f"{w:.1f}x")

        await interaction.followup.send(
            embed=success_embed("Stereo Enhance", t("stereo.set", locale, mode=label)),
            ephemeral=True,
        )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(PanCog(bot))
