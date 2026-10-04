# -*- coding: utf-8 -*-
"""
seek/cog.py — Slash commands for Seek, Fast-Forward, Rewind, and Replay (Features 2.1–2.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from seek.parser import parse_time_string
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed
from utils.formatters import format_duration

if TYPE_CHECKING:
    from main import MusicBot


class SeekCog(commands.Cog, name="Seek"):
    """Playback seeking, fast-forward, rewind, and replay controls."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Verify the user is connected to voice in the correct channel and has DJ/Admin permissions."""
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

        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    @app_commands.command(name="seek", description="Seek to a specific timestamp in the current track")
    @app_commands.describe(time="Timestamp to seek to (e.g. 1:30, 90, 1m20s, 01:15:00)")
    async def seek(self, interaction: discord.Interaction, time: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Not Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        target_sec = parse_time_string(time)
        if target_sec is None:
            await interaction.followup.send(
                embed=error_embed("Invalid Timestamp", t("seek.invalid_format", locale)),
                ephemeral=True,
            )
            return

        dur = player.now_playing.duration
        if dur > 0 and target_sec >= dur:
            dur_str = format_duration(dur)
            await interaction.followup.send(
                embed=error_embed("Out of Range", t("seek.out_of_range", locale, duration=dur_str)),
                ephemeral=True,
            )
            return

        ok = await self.bot.seek.seek_to(interaction.guild_id, target_sec)
        if ok:
            time_str = format_duration(target_sec)
            await interaction.followup.send(
                embed=success_embed("Seek", t("seek.success", locale, time=time_str)),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Seek Failed", t("error.unexpected", locale)),
                ephemeral=True,
            )

    @app_commands.command(name="forward", description="Fast-forward playback by specified seconds")
    @app_commands.describe(seconds="Number of seconds to skip forward (default: 15)")
    async def forward(self, interaction: discord.Interaction, seconds: int = 15) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        if seconds <= 0:
            seconds = 15

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Not Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        ok, new_time = await self.bot.seek.forward(interaction.guild_id, seconds)
        if ok:
            time_str = format_duration(new_time)
            await interaction.followup.send(
                embed=success_embed("Fast-Forward", t("seek.forward", locale, seconds=seconds, time=time_str)),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Forward Failed", t("error.unexpected", locale)),
                ephemeral=True,
            )

    @app_commands.command(name="rewind", description="Rewind playback by specified seconds")
    @app_commands.describe(seconds="Number of seconds to rewind (default: 15)")
    async def rewind(self, interaction: discord.Interaction, seconds: int = 15) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        if seconds <= 0:
            seconds = 15

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Not Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        ok, new_time = await self.bot.seek.rewind(interaction.guild_id, seconds)
        if ok:
            time_str = format_duration(new_time)
            await interaction.followup.send(
                embed=success_embed("Rewind", t("seek.rewind", locale, seconds=seconds, time=time_str)),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Rewind Failed", t("error.unexpected", locale)),
                ephemeral=True,
            )

    async def _replay_impl(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Not Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        track_title = player.now_playing.short_title
        ok = await self.bot.seek.replay(interaction.guild_id)
        if ok:
            await interaction.followup.send(
                embed=success_embed("Replay", t("seek.replay", locale, title=track_title)),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Replay Failed", t("error.unexpected", locale)),
                ephemeral=True,
            )

    @app_commands.command(name="replay", description="Replay the current track from the beginning")
    async def replay(self, interaction: discord.Interaction) -> None:
        await self._replay_impl(interaction)

    @app_commands.command(name="restart", description="Restart the current track from the beginning")
    async def restart(self, interaction: discord.Interaction) -> None:
        await self._replay_impl(interaction)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(SeekCog(bot))
