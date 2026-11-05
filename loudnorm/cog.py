# -*- coding: utf-8 -*-
"""
loudnorm/cog.py — Discord slash command for EBU R128 Loudness Normalization (Feature 2.6 & Gen 5.0).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from loudnorm.filter import LOUDNORM_PROFILES
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


class LoudnormCog(commands.Cog, name="Loudnorm"):
    """EBU R128 Smart Loudness Normalization control (Feature 2.6 & Gen 5.0)."""

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
        description="EBU R128 smart loudness normalization across varied audio tracks",
    )
    @app_commands.describe(
        profile="Select loudness target profile (broadcast, podcast, background, party)",
        enabled="Enable or disable EBU R128 Loudnorm (leave blank to toggle)",
    )
    @app_commands.choices(profile=[
        app_commands.Choice(name="📻 Broadcast Standard (I=-16, TP=-1.5, LRA=11)", value="broadcast"),
        app_commands.Choice(name="🎙️ Podcast / Spoken Word (I=-14, TP=-1.0, LRA=7)", value="podcast"),
        app_commands.Choice(name="☕ Background / Casual (I=-20, TP=-2.0, LRA=14)", value="background"),
        app_commands.Choice(name="🎉 Party / Club Punch (I=-12, TP=-0.5, LRA=8)", value="party"),
    ])
    async def loudnorm(
        self,
        interaction: discord.Interaction,
        profile: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if profile is not None:
            norm_prof = profile.lower().strip()
            if norm_prof in LOUDNORM_PROFILES:
                player.loudnorm_profile = norm_prof
                player.loudnorm = True
        elif enabled is not None:
            player.loudnorm = enabled
        else:
            player.loudnorm = not player.loudnorm

        state = player.loudnorm
        self._restart_audio(interaction.guild_id)

        active_prof = getattr(player, "loudnorm_profile", "broadcast")
        prof_info = LOUDNORM_PROFILES.get(active_prof, LOUDNORM_PROFILES["broadcast"])

        msg = t("loudnorm.enabled", locale) if state else t("loudnorm.disabled", locale)
        profile_notice = t("loudnorm.profile_set", locale, profile=active_prof.capitalize())

        desc = (
            f"{msg}\n"
            f"{profile_notice}\n\n"
            f"🎯 *Profile Targets: Integrated `{prof_info['i']} LUFS`, True Peak `{prof_info['tp']} dBTP`, Range `{prof_info['lra']} LU`*\n"
            "Eliminates abrupt volume jumps while preserving dynamic audio range."
            if state else
            f"{msg}\n\n🔊 *Tracks play at their original raw mastering volume level.*"
        )

        embed = discord.Embed(
            title="🎚️ Smart Loudness Normalization (EBU R128)",
            description=desc,
            color=0x2ED573 if state else 0x747F8D,
        )
        embed.set_footer(text=f"Profile: {active_prof.upper()} • ITU-R BS.1770 / EBU R128")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LoudnormCog(bot))
