# -*- coding: utf-8 -*-
"""
loop_ab/cog.py — Discord slash command for Loop A-B segment repeat (Feature 2.7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from loop_ab.service import LoopABService
from seek.parser import parse_time_string
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed
from utils.formatters import format_duration

if TYPE_CHECKING:
    from main import MusicBot


class LoopABCog(commands.Cog, name="LoopAB"):
    """Loop A-B audio segment repetition controls (Feature 2.7)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self.service = LoopABService(bot)

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

    @app_commands.command(
        name="loopab",
        description="Loop a specific segment of the track (e.g. /loopab 0:30 1:15 or /loopab off)",
    )
    @app_commands.describe(
        start="Start timestamp (e.g. 0:30, 45s) or type 'off' to disable",
        end="End timestamp (e.g. 1:15, 90s)",
    )
    async def loopab(
        self,
        interaction: discord.Interaction,
        start: str,
        end: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)

        # Handling 'off', 'stop', 'clear' shortcut in start argument
        if start.lower().strip() in ("off", "stop", "clear", "cancel"):
            stopped = self.service.stop_loop(interaction.guild_id)
            if stopped:
                await interaction.followup.send(
                    embed=success_embed("Loop A-B", t("loopab.disabled", locale)),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=error_embed("Loop A-B", t("loopab.not_active", locale)),
                    ephemeral=True,
                )
            return

        # Status check shortcut
        if start.lower().strip() == "status":
            status = self.service.get_status(interaction.guild_id)
            if status:
                s_str = format_duration(status[0])
                e_str = format_duration(status[1])
                await interaction.followup.send(
                    embed=success_embed("Loop A-B", t("loopab.started", locale, start=s_str, end=e_str)),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=error_embed("Loop A-B", t("loopab.not_active", locale)),
                    ephemeral=True,
                )
            return

        # Expecting both start and end timestamps
        if not end:
            await interaction.followup.send(
                embed=error_embed(
                    "Missing End Time",
                    "Please provide both start and end timestamps (e.g. `/loopab 0:30 1:15`) or use `/loopab off`.",
                ),
                ephemeral=True,
            )
            return

        start_sec = parse_time_string(start)
        end_sec = parse_time_string(end)

        if start_sec is None or end_sec is None:
            await interaction.followup.send(
                embed=error_embed("Invalid Timestamps", t("seek.invalid_format", locale)),
                ephemeral=True,
            )
            return

        if end_sec <= start_sec:
            await interaction.followup.send(
                embed=error_embed(
                    "Invalid Range",
                    t("loopab.invalid_times", locale, start=format_duration(start_sec), end=format_duration(end_sec)),
                ),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Not Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        ok, err = await self.service.start_loop(interaction.guild_id, start_sec, end_sec)
        if ok:
            start_formatted = format_duration(start_sec)
            end_formatted = format_duration(end_sec)
            await interaction.followup.send(
                embed=success_embed(
                    "Loop A-B Enabled",
                    t("loopab.started", locale, start=start_formatted, end=end_formatted),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Loop A-B Failed", t("error.unexpected", locale)),
                ephemeral=True,
            )

    @app_commands.command(name="loopab_off", description="Turn off active Loop A-B repetition")
    async def loopab_off(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        stopped = self.service.stop_loop(interaction.guild_id)
        if stopped:
            await interaction.followup.send(
                embed=success_embed("Loop A-B", t("loopab.disabled", locale)),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Loop A-B", t("loopab.not_active", locale)),
                ephemeral=True,
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LoopABCog(bot))
