# -*- coding: utf-8 -*-
"""
cogs/admin.py — Admin / server configuration commands for Music Bot V3 (NEW).

Commands:
  /djset role @role     — Set DJ role (admin only)
  /djset clear          — Remove DJ role restriction
  /requestchannel set   — Set dedicated music request channel
  /requestchannel clear — Remove request channel
  /autoplaylist on/off  — Toggle auto-playlist for this guild
  /idletimeout <secs>   — Set idle auto-disconnect timer
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import (
    error_embed, success_embed, info_embed,
    dj_set_embed, dj_cleared_embed,
    request_channel_set_embed,
)

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


def _is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


class AdminCog(commands.Cog, name="Admin"):
    """Server configuration commands (administrator only)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── /djset ────────────────────────────────────────────────────────────────

    djset_group = app_commands.Group(
        name              = "djset",
        description       = "Configure DJ role for this server",
        default_permissions = discord.Permissions(administrator=True),
    )

    @djset_group.command(name="role", description="Set the DJ role (only this role can control the bot)")
    @app_commands.describe(role="Role to designate as DJ")
    async def djset_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.dj_role_id = role.id
        cfg.dj_only    = True
        await self.bot.db.save_server_config(cfg)

        await interaction.followup.send(embed=dj_set_embed(role), ephemeral=True)

    @djset_group.command(name="clear", description="Remove DJ role restriction (everyone can control the bot)")
    async def djset_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.dj_role_id = None
        cfg.dj_only    = False
        await self.bot.db.save_server_config(cfg)

        await interaction.followup.send(embed=dj_cleared_embed(), ephemeral=True)

    # ── /requestchannel ───────────────────────────────────────────────────────

    rc_group = app_commands.Group(
        name              = "requestchannel",
        description       = "Configure the dedicated music request channel",
        default_permissions = discord.Permissions(administrator=True),
    )

    @rc_group.command(name="set", description="Set a channel where users type song names to play")
    @app_commands.describe(channel="Text channel to designate as the request channel")
    async def rc_set(
        self,
        interaction: discord.Interaction,
        channel:     discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.request_channel_id = channel.id
        await self.bot.db.save_server_config(cfg)

        await interaction.followup.send(embed=request_channel_set_embed(channel), ephemeral=True)

    @rc_group.command(name="clear", description="Remove the request channel")
    async def rc_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.request_channel_id = None
        await self.bot.db.save_server_config(cfg)

        await interaction.followup.send(embed=success_embed("Request Channel Cleared"), ephemeral=True)

    # ── /autoplaylist ─────────────────────────────────────────────────────────

    @app_commands.command(
        name        = "autoplaylist",
        description = "Toggle auto-playlist (fills queue from history when empty)",
    )
    @app_commands.describe(enabled="on or off")
    @app_commands.default_permissions(administrator=True)
    async def autoplaylist(self, interaction: discord.Interaction, enabled: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        val = enabled.strip().lower()
        if val not in ("on", "off", "true", "false", "1", "0"):
            await interaction.followup.send(
                embed=error_embed("Invalid Value", 'Use "on" or "off".'), ephemeral=True
            )
            return

        is_on = val in ("on", "true", "1")
        cfg   = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.auto_playlist = is_on
        await self.bot.db.save_server_config(cfg)

        # Also toggle the live player flag
        player = self.bot.get_player(interaction.guild_id)
        player.auto_playlist_mode = is_on

        status = "enabled ✅" if is_on else "disabled ❌"
        await interaction.followup.send(
            embed=success_embed("Auto-Playlist", f"Auto-playlist has been **{status}** for this server."),
            ephemeral=True,
        )

    @autoplaylist.autocomplete("enabled")
    async def autoplaylist_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name="On",  value="on"),
            app_commands.Choice(name="Off", value="off"),
        ]

    # ── /idletimeout ──────────────────────────────────────────────────────────

    @app_commands.command(
        name        = "idletimeout",
        description = "Set idle auto-disconnect timeout (seconds)",
    )
    @app_commands.describe(seconds="Seconds before auto-disconnect when idle (60-3600)")
    @app_commands.default_permissions(administrator=True)
    async def idletimeout(self, interaction: discord.Interaction, seconds: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Administrator permission required."), ephemeral=True
            )
            return

        if not 60 <= seconds <= 3600:
            await interaction.followup.send(
                embed=error_embed("Invalid Value", "Idle timeout must be between 60 and 3600 seconds."),
                ephemeral=True,
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.idle_timeout = seconds
        await self.bot.db.save_server_config(cfg)

        from utils.formatters import format_duration
        await interaction.followup.send(
            embed=success_embed(
                "Idle Timeout Set",
                f"Bot will auto-disconnect after **{format_duration(seconds)}** of silence.",
            ),
            ephemeral=True,
        )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(AdminCog(bot))
