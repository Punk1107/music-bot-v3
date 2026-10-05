# -*- coding: utf-8 -*-
"""
loudnorm/cog.py — Discord slash command for EBU R128 Loudness Normalization (Feature 2.6).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


class LoudnormCog(commands.Cog, name="Loudnorm"):
    """EBU R128 Smart Loudness Normalization control (Feature 2.6)."""

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
                embed=error_embed("Not in Voice", t("error.not_in_voice", locale)),
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if vc and vc.channel != member.voice.channel:
            await interaction.followup.send(
                embed=error_embed("Wrong Channel", t("error.wrong_channel", locale, channel=vc.channel.name)),
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
        """Hot-reload current playback to apply/remove loudnorm seamlessly."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"loudnorm_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    @app_commands.command(
        name="loudnorm",
        description="Toggle EBU R128 Smart Loudness Normalization (balances volume across tracks)",
    )
    @app_commands.describe(enabled="Enable or disable EBU R128 Loudnorm (leave blank to toggle)")
    async def loudnorm(self, interaction: discord.Interaction, enabled: Optional[bool] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if enabled is None:
            player.loudnorm = not player.loudnorm
        else:
            player.loudnorm = enabled

        state = player.loudnorm
        self._restart_audio(interaction.guild_id)

        msg = t("loudnorm.enabled", locale) if state else t("loudnorm.disabled", locale)
        desc = (
            f"{msg}\n\n"
            + (
                "🎯 *EBU R128 integrated loudness target: -16 LUFS, True Peak: -1.5 dBTP.*\n"
                "Prevents quiet acoustic songs from being inaudible and loud EDM/rock from blowing out your ears."
                if state else
                "🔊 *Tracks play at their original raw mastering volume level.*"
            )
        )
        embed = discord.Embed(
            title="🎚️ Smart Loudness Normalization (EBU R128)",
            description=desc,
            color=0x2ED573 if state else 0x747F8D,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LoudnormCog(bot))
