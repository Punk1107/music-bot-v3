# -*- coding: utf-8 -*-
"""remaster/cog.py — Discord slash commands for Audio Remaster & Clarity Enhancer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from remaster.filter import build_clarity_filter, build_remaster_filter
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


class RemasterCog(commands.Cog, name="Remaster"):
    """Studio Audio Remaster & Clarity Enhancer (Feature Gen 5.0)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Verify the user is connected to voice and has DJ/Admin permissions."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False

        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not member.voice or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.not_in_voice", locale), t("error.not_in_voice", locale)),
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if vc and vc.channel != member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.wrong_channel", locale), t("error.wrong_channel", locale, channel=vc.channel.name)),
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

        await interaction.followup.send(embed=dj_required_embed(interaction), ephemeral=True)
        return False

    def _restart_audio(self, guild_id: int) -> None:
        """Hot-reload current playback to apply remaster / clarity filters seamlessly."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"remaster_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    @app_commands.command(
        name="remaster",
        description="Toggle Studio Remaster (crystalizer high-frequency + 45Hz sub-bass punch)",
    )
    @app_commands.describe(enabled="Enable or disable remaster mode (leave blank to toggle)")
    async def remaster(self, interaction: discord.Interaction, enabled: Optional[bool] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if enabled is None:
            player.remaster = not player.remaster
        else:
            player.remaster = enabled

        state = player.remaster
        self._restart_audio(interaction.guild_id)

        msg = t("remaster.enabled", locale) if state else t("remaster.disabled", locale)
        embed = discord.Embed(
            title="💎 Studio Remaster Engine",
            description=msg,
            color=0x2ED573 if state else 0x747F8D,
        )
        if state:
            embed.set_footer(
                text=f"Clarity Level: {player.clarity_level}/5 • Sub-Harmonic Compand Active"
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clarity",
        description="Set high-frequency harmonic restoration level (crystalizer 1-5)",
    )
    @app_commands.describe(level="Clarity level (1 = Subtle, 5 = Maximum, <= 3.0 safe cap)")
    @app_commands.choices(level=[
        app_commands.Choice(name="Level 1 — Subtle (i=1.4)", value=1),
        app_commands.Choice(name="Level 2 — Balanced (i=1.8, Default)", value=2),
        app_commands.Choice(name="Level 3 — Crisp (i=2.2)", value=3),
        app_commands.Choice(name="Level 4 — High Definition (i=2.6)", value=4),
        app_commands.Choice(name="Level 5 — Ultra Detail (i=3.0, Max)", value=5),
    ])
    async def clarity(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        clamped = max(1, min(5, int(level)))
        player.clarity_level = clamped
        # Automatically enable remaster mode if not already active
        player.remaster = True

        self._restart_audio(interaction.guild_id)

        embed = discord.Embed(
            title="💎 Audio Clarity & Harmonic Detail",
            description=t("clarity.level_set", locale, level=clamped),
            color=0x1ABC9C,
        )
        embed.set_footer(text=f"Crystalizer Intensity: {1.0 + clamped * 0.4:.1f} • Pure FFmpeg Math")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(RemasterCog(bot))

