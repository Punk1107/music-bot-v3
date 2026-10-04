# -*- coding: utf-8 -*-
"""cogs/effects.py — Audio effects and volume commands for Music Bot V3."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from models.enums import AudioEffect, AudioQuality
from core.i18n import get_locale, t
from utils.embeds import error_embed, success_embed, info_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class EffectsCog(commands.Cog, name="Effects"):
    """Audio effects and volume control."""

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

    def _restart_audio(self, guild_id: int) -> None:
        """Hot-reload FFmpeg with updated options without advancing the queue."""
        import asyncio
        if hasattr(self.bot, "seek") and self.bot.seek:
            asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            vc = guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()

    @app_commands.command(name="volume", description="Set playback volume (0–200%)")
    @app_commands.describe(level="Volume percentage 0-200")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not 0 <= level <= 200:
            await interaction.followup.send(
                embed=error_embed("Invalid Volume", t("volume.invalid", locale)), ephemeral=True
            )
            return
        player = self.bot.get_player(interaction.guild_id)
        player.volume = level / 100
        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if vc and vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = player.volume
        else:
            self._restart_audio(interaction.guild_id)
        await interaction.followup.send(
            embed=success_embed("Volume Set", t("volume.set", locale, vol=level)), ephemeral=True
        )

    @app_commands.command(name="effects", description="Toggle one of 18 audio effects")
    @app_commands.describe(effect="Effect to toggle")
    async def effects(self, interaction: discord.Interaction, effect: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        try:
            eff = AudioEffect(effect)
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Unknown Effect", f"No effect named `{effect}`. Use /effects_list."),
                ephemeral=True,
            )
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        if eff in player.effects:
            player.effects.remove(eff)
            action = "disabled"
            desc = f"{eff.display_name()} has been disabled."
        else:
            player.effects.append(eff)
            action = "enabled"
            desc = t("effects.applied", locale, effect=eff.display_name())

        self._restart_audio(interaction.guild_id)
        await interaction.followup.send(
            embed=success_embed(f"Effect {action.capitalize()}", desc),
            ephemeral=True,
        )

    @effects.autocomplete("effect")
    async def effects_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=e.display_name(), value=e.value)
            for e in AudioEffect
            if current.lower() in e.display_name().lower() or current.lower() in e.value
        ][:25]

    @app_commands.command(name="effects_clear", description="Disable all active audio effects")
    async def effects_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        player.effects.clear()
        self._restart_audio(interaction.guild_id)
        await interaction.followup.send(embed=success_embed("Effects Cleared", t("effects.cleared", locale)), ephemeral=True)

    @app_commands.command(name="effects_list", description="Show all 18 effects with current status")
    async def effects_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.bot.get_player(interaction.guild_id)
        active = set(player.effects)
        lines = []
        for eff in AudioEffect:
            status = "✅" if eff in active else "⬜"
            lines.append(f"{status} {eff.display_name()}")
        embed = discord.Embed(
            title       = "🎛 Audio Effects",
            description = "\n".join(lines),
            color       = 0x5865F2,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="quality", description="Set audio quality preset")
    @app_commands.describe(preset="low / medium / high / ultra")
    async def quality(self, interaction: discord.Interaction, preset: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        try:
            q = AudioQuality(preset.lower())
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Invalid Preset", "Use: low / medium / high / ultra"), ephemeral=True
            )
            return
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.audio_quality = q
        await self.bot.db.save_server_config(cfg)
        self._restart_audio(interaction.guild_id)
        await interaction.followup.send(
            embed=success_embed("Quality Set", f"Audio quality: **{q.value.upper()}**"), ephemeral=True
        )

    @quality.autocomplete("preset")
    async def quality_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=q.value.upper(), value=q.value)
            for q in AudioQuality
            if current.lower() in q.value
        ]


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(EffectsCog(bot))
