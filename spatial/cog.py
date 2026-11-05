# -*- coding: utf-8 -*-
"""spatial/cog.py — Discord slash commands for Spatial 3D & Binaural Virtualization."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from spatial.filter import SPATIAL_PRESETS, build_spatial_filter
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


class SpatialCog(commands.Cog, name="Spatial"):
    """Spatial 3D & Binaural Headphone Virtualizer (Feature Gen 5.0)."""

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
        """Hot-reload current playback to apply new spatial filter seamlessly."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"spatial_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    @app_commands.command(
        name="spatial",
        description="Spatial 3D & binaural virtualizer for headphone acoustic immersion",
    )
    @app_commands.describe(preset="Select room acoustics or headphone 3D preset")
    @app_commands.choices(preset=[
        app_commands.Choice(name="🎧 Binaural (Earwax HRTF Virtualizer)", value="binaural"),
        app_commands.Choice(name="🏛️ Concert Hall (Wide Reverb & Surround)", value="hall"),
        app_commands.Choice(name="🎙️ Intimate Studio (Dry Damped Acoustics)", value="studio"),
        app_commands.Choice(name="🎛️ Club Arena (Punchy Bass & Reverb)", value="club"),
        app_commands.Choice(name="❌ Off (Standard Stereo)", value="off"),
    ])
    async def spatial(self, interaction: discord.Interaction, preset: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        norm_preset = preset.lower().strip()
        if norm_preset in ("off", "none", "disable"):
            player.spatial_preset = None
        elif norm_preset in SPATIAL_PRESETS:
            player.spatial_preset = norm_preset
        else:
            await interaction.followup.send(
                embed=error_embed("Invalid Preset", "Use: binaural, hall, studio, club, or off"),
                ephemeral=True,
            )
            return

        self._restart_audio(interaction.guild_id)

        if not player.spatial_preset:
            embed = discord.Embed(
                title="🎧 Spatial 3D Audio",
                description=t("spatial.off", locale),
                color=0x747F8D,
            )
        else:
            name_map = {
                "binaural": "🎧 Binaural Headphone Virtualizer (Earwax)",
                "hall":     "🏛️ Concert Hall (Wide Reverb)",
                "studio":   "🎙️ Intimate Studio Room",
                "club":     "🎛️ Club Arena (+4dB Bass)",
            }
            display_name = name_map.get(player.spatial_preset, player.spatial_preset.capitalize())
            embed = discord.Embed(
                title="🎧 Spatial 3D Audio Virtualizer",
                description=t("spatial.preset_set", locale, preset=display_name),
                color=0x2ED573,
            )
            embed.set_footer(text="Pure FFmpeg HRTF & Acoustic Simulation")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(SpatialCog(bot))

