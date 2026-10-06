# -*- coding: utf-8 -*-
"""
equalizer/cog.py — Discord slash commands for 4-Band Frequency Equalizer (Feature 2.5).
Provides /equalizer and /eq commands with presets, custom gains, reset, and visualizer.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from equalizer.presets import EQ_BANDS, EQ_PRESETS, PRESET_NAMES, clamp_gain
from utils.embeds import error_embed, info_embed, success_embed
from utils.error_handler import dj_required_embed

if TYPE_CHECKING:
    from main import MusicBot


def _render_bar(gain: float, min_val: float = -10.0, max_val: float = 10.0, length: int = 12) -> str:
    """Render a clean text-based progress bar for gain level."""
    ratio = (gain - min_val) / (max_val - min_val)
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * length))
    return f"`[{'=' * filled}{'-' * (length - filled)}]`"


class EqualizerCog(commands.Cog, name="Equalizer"):
    """4-Band Frequency Equalizer and Audio Presets (Feature 2.5)."""

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
        """Seamlessly hot-reload FFmpeg with new equalizer filters without advancing queue."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"eq_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    # ── Subcommand implementation helpers ────────────────────────────────────

    async def _apply_preset(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        key = name.lower().strip()
        if key not in EQ_PRESETS:
            await interaction.followup.send(
                embed=error_embed(
                    "Invalid Preset",
                    t("equalizer.invalid_preset", locale, name=name, presets=", ".join(PRESET_NAMES)),
                ),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        player.equalizer_bands = EQ_PRESETS[key].copy()
        player.equalizer_preset = key
        self._restart_audio(interaction.guild_id)

        await interaction.followup.send(
            embed=success_embed(
                "Equalizer",
                t("equalizer.preset_applied", locale, name=key.capitalize()),
            ),
            ephemeral=True,
        )

    async def _apply_custom(
        self,
        interaction: discord.Interaction,
        sub_bass: float,
        bass: float,
        mid: float,
        treble: float,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        for g in (sub_bass, bass, mid, treble):
            if not -10.0 <= g <= 10.0:
                await interaction.followup.send(
                    embed=error_embed("Invalid Gain", t("equalizer.invalid_gain", locale)),
                    ephemeral=True,
                )
                return

        player = self.bot.get_player(interaction.guild_id)
        player.equalizer_bands = {
            "sub_bass": clamp_gain(sub_bass),
            "bass":     clamp_gain(bass),
            "mid":      clamp_gain(mid),
            "treble":   clamp_gain(treble),
        }
        player.equalizer_preset = "custom"
        self._restart_audio(interaction.guild_id)

        await interaction.followup.send(
            embed=success_embed(
                "Equalizer",
                t(
                    "equalizer.custom_applied",
                    locale,
                    sub_bass=f"{sub_bass:+.1f}",
                    bass=f"{bass:+.1f}",
                    mid=f"{mid:+.1f}",
                    treble=f"{treble:+.1f}",
                ),
            ),
            ephemeral=True,
        )

    async def _reset_eq(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        player.equalizer_bands = {}
        player.equalizer_preset = None
        self._restart_audio(interaction.guild_id)

        await interaction.followup.send(
            embed=success_embed("Equalizer", t("equalizer.reset", locale)),
            ephemeral=True,
        )

    async def _show_eq(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.bot.get_player(interaction.guild_id)
        preset_label = (player.equalizer_preset or "Flat").capitalize()
        bands = player.equalizer_bands or {}

        lines = [f"**Active Preset**: `{preset_label}`\n"]
        for key, meta in EQ_BANDS.items():
            gain = bands.get(key, 0.0)
            bar = _render_bar(gain)
            lines.append(f"**{meta['label']}**: {bar} `{gain:+.1f} dB`")

        embed = discord.Embed(
            title="🎚️ 4-Band Frequency Equalizer",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text="Use /equalizer preset, /equalizer custom, or /equalizer reset to adjust.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Slash Command Group: /equalizer ──────────────────────────────────────

    eq_group = app_commands.Group(name="equalizer", description="4-Band Frequency Equalizer commands")

    @eq_group.command(name="preset", description="Apply an equalizer preset (Pop, Rock, Electronic, Vocal, Flat)")
    @app_commands.describe(name="Preset to apply")
    @app_commands.choices(name=[
        app_commands.Choice(name="Pop",        value="pop"),
        app_commands.Choice(name="Rock",       value="rock"),
        app_commands.Choice(name="Electronic", value="electronic"),
        app_commands.Choice(name="Vocal",      value="vocal"),
        app_commands.Choice(name="Flat",       value="flat"),
    ])
    async def eq_preset(self, interaction: discord.Interaction, name: str) -> None:
        await self._apply_preset(interaction, name)

    @eq_group.command(name="custom", description="Set custom gain levels (-10dB to +10dB) for each frequency band")
    @app_commands.describe(
        sub_bass="Sub-bass gain in dB (-10 to +10)",
        bass="Bass gain in dB (-10 to +10)",
        mid="Mid-range gain in dB (-10 to +10)",
        treble="Treble gain in dB (-10 to +10)",
    )
    async def eq_custom(
        self,
        interaction: discord.Interaction,
        sub_bass: float,
        bass: float,
        mid: float,
        treble: float,
    ) -> None:
        await self._apply_custom(interaction, sub_bass, bass, mid, treble)

    @eq_group.command(name="reset", description="Reset equalizer to Flat (0 dB on all bands)")
    async def eq_reset(self, interaction: discord.Interaction) -> None:
        await self._reset_eq(interaction)

    @eq_group.command(name="show", description="Display current equalizer band levels and visualizer")
    async def eq_show(self, interaction: discord.Interaction) -> None:
        await self._show_eq(interaction)

    @eq_group.command(name="view", description="Display current equalizer band levels and visualizer")
    async def eq_view(self, interaction: discord.Interaction) -> None:
        await self._show_eq(interaction)

    # ── Slash Command: /eq shorthand ─────────────────────────────────────────

    @app_commands.command(name="eq", description="Quick shortcut to apply an equalizer preset")
    @app_commands.describe(preset="Preset to apply (or choose Flat to reset)")
    @app_commands.choices(preset=[
        app_commands.Choice(name="Pop",        value="pop"),
        app_commands.Choice(name="Rock",       value="rock"),
        app_commands.Choice(name="Electronic", value="electronic"),
        app_commands.Choice(name="Vocal",      value="vocal"),
        app_commands.Choice(name="Flat",       value="flat"),
    ])
    async def eq_shortcut(self, interaction: discord.Interaction, preset: str) -> None:
        await self._apply_preset(interaction, preset)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(EqualizerCog(bot))
