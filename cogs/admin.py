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

from core.i18n import get_locale, t
from utils.embeds import (
    error_embed, success_embed, info_embed,
    dj_set_embed, dj_cleared_embed,
    request_channel_set_embed,
)

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


def _is_admin(member: discord.Member) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and perms.administrator)


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
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.dj_role_id = role.id
        cfg.dj_only    = True
        await self.bot.db.save_server_config(cfg)

        locale = await get_locale(interaction.guild_id, self.bot.db)
        await interaction.followup.send(embed=dj_set_embed(role, locale=locale), ephemeral=True)

    @djset_group.command(name="clear", description="Remove DJ role restriction (everyone can control the bot)")
    async def djset_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.dj_role_id = None
        cfg.dj_only    = False
        await self.bot.db.save_server_config(cfg)

        locale = await get_locale(interaction.guild_id, self.bot.db)
        await interaction.followup.send(embed=dj_cleared_embed(locale=locale), ephemeral=True)

    # ── /requestchannel ───────────────────────────────────────────────────────

    rc_group = app_commands.Group(
        name              = "requestchannel",
        description       = "Configure the dedicated music request channel",
        default_permissions = discord.Permissions(administrator=True),
    )

    @rc_group.command(name="set", description="Set a channel where users type song names to play")
    @app_commands.describe(
        channel="Text channel to designate as the request channel",
        clean_mode="Enable clean mode (single message board, user query auto-deleted)",
    )
    async def rc_set(
        self,
        interaction: discord.Interaction,
        channel:     discord.TextChannel,
        clean_mode:  bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        # Delete old board message if present
        if cfg.request_channel_id and cfg.board_message_id and interaction.guild:
            try:
                ch = interaction.guild.get_channel(cfg.request_channel_id)
                if isinstance(ch, discord.TextChannel):
                    old_msg = await ch.fetch_message(cfg.board_message_id)
                    await old_msg.delete()
            except Exception as e:
                logger.debug("Failed deleting old board message on rc_set: %s", e)
        cfg.board_message_id = None
        cfg.request_channel_id = channel.id
        cfg.clean_mode = clean_mode

        if clean_mode:
            from utils.embeds import request_channel_board_embed
            from utils.views import CleanBoardControlView
            player = self.bot.get_player(interaction.guild_id)
            embed = request_channel_board_embed(player, locale=locale)
            view = CleanBoardControlView(self.bot, interaction.guild_id)
            try:
                board_msg = await channel.send(embed=embed, view=view)
                cfg.board_message_id = board_msg.id
            except Exception as e:
                logger.warning("Failed sending clean board message: %s", e)

        await self.bot.db.save_server_config(cfg)

        locale = await get_locale(interaction.guild_id, self.bot.db)
        await interaction.followup.send(embed=request_channel_set_embed(channel, locale=locale), ephemeral=True)

    @rc_group.command(name="clean_mode", description="Toggle single-message clean player board mode")
    @app_commands.describe(enabled="Enable or disable clean player board mode")
    async def rc_clean_mode(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.request_channel_id:
            await interaction.followup.send("❌ Please set a request channel first with `/requestchannel set`.", ephemeral=True)
            return

        cfg.clean_mode = enabled
        if enabled and interaction.guild:
            channel = interaction.guild.get_channel(cfg.request_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                if cfg.board_message_id:
                    try:
                        old_msg = await channel.fetch_message(cfg.board_message_id)
                        await old_msg.delete()
                    except Exception as e:
                        logger.debug("Failed deleting old board message on rc_clean_mode enable: %s", e)
                    cfg.board_message_id = None
                from utils.embeds import request_channel_board_embed
                from utils.views import CleanBoardControlView
                player = self.bot.get_player(interaction.guild_id)
                embed = request_channel_board_embed(player, locale=locale)
                view = CleanBoardControlView(self.bot, interaction.guild_id)
                try:
                    board_msg = await channel.send(embed=embed, view=view)
                    cfg.board_message_id = board_msg.id
                except Exception as e:
                    logger.warning("Failed sending clean board message: %s", e)
        elif not enabled:
            if cfg.board_message_id and interaction.guild and cfg.request_channel_id:
                try:
                    ch = interaction.guild.get_channel(cfg.request_channel_id)
                    if isinstance(ch, discord.TextChannel):
                        old_msg = await ch.fetch_message(cfg.board_message_id)
                        await old_msg.delete()
                except Exception as e:
                    logger.debug("Failed deleting old board message on disable: %s", e)
            cfg.board_message_id = None

        await self.bot.db.save_server_config(cfg)
        status_text = "enabled" if enabled else "disabled"
        desc = t("clean_mode.updated", locale, status=status_text, channel=cfg.request_channel_id)
        await interaction.followup.send(
            embed=success_embed("Clean Mode", desc),
            ephemeral=True,
        )

    @rc_group.command(name="clear", description="Remove the request channel")
    async def rc_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if cfg.board_message_id and interaction.guild and cfg.request_channel_id:
            try:
                ch = interaction.guild.get_channel(cfg.request_channel_id)
                if isinstance(ch, discord.TextChannel):
                    old_msg = await ch.fetch_message(cfg.board_message_id)
                    await old_msg.delete()
            except Exception as e:
                logger.debug("Failed deleting old board message on clear: %s", e)

        cfg.request_channel_id = None
        cfg.board_message_id = None
        cfg.clean_mode = False
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
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        val = enabled.strip().lower()
        if val not in ("on", "off", "true", "false", "1", "0"):
            await interaction.followup.send(
                embed=error_embed(t("title.invalid_value", locale), 'Use "on" or "off".'), ephemeral=True
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
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."), ephemeral=True
            )
            return

        if not 60 <= seconds <= 3600:
            await interaction.followup.send(
                embed=error_embed(t("title.invalid_value", locale), "Idle timeout must be between 60 and 3600 seconds."),
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

    # ── /autoresume ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="autoresume",
        description="Configure automatic voice reconnection and playback resumption on reboot",
    )
    @app_commands.describe(state="Enable or disable auto-resume on bot restart (on/off)")
    @app_commands.choices(state=[
        app_commands.Choice(name="On (Automatically resume playback)", value="on"),
        app_commands.Choice(name="Off (Disabled)", value="off"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def autoresume(
        self,
        interaction: discord.Interaction,
        state: str = "on",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not _is_admin(interaction.user):
            await interaction.followup.send(
                embed=error_embed(t("title.permission_denied", locale), "Administrator permission required."),
                ephemeral=True,
            )
            return

        is_on = state.lower().strip() == "on"
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.auto_resume = is_on
        await self.bot.db.save_server_config(cfg)
        if not is_on and hasattr(self.bot.db, "clear_session_state"):
            await self.bot.db.clear_session_state(interaction.guild_id)

        status_str = "enabled" if is_on else "disabled"
        desc = t("autoresume.status", locale, status=status_str)
        embed = success_embed("Auto-Resume 2.0", desc)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(AdminCog(bot))
