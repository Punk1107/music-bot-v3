# -*- coding: utf-8 -*-
"""
scheduler/cog.py — Discord Slash Commands for Server Music Scheduler & Alarm Clock.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import t, get_locale
from scheduler.service import MusicSchedulerService

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


def _is_admin(user: discord.Member | discord.User) -> bool:
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator
    return False


class SchedulerCog(commands.Cog, name="Scheduler"):
    """Server Music Scheduler & Alarm Clock Cog."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self.service = MusicSchedulerService(bot)

    schedule_group = app_commands.Group(
        name="schedule",
        description="Server Music Scheduler & Alarm Clock powered by MySQL",
        default_permissions=discord.Permissions(administrator=True),
    )

    @schedule_group.command(name="add", description="Schedule music to play automatically at a specific time")
    @app_commands.describe(
        time="Alarm time in 24hr format HH:MM (e.g., 08:00 or 18:30)",
        query="Song title, artist, or direct URL to play",
        voice_channel="Voice channel for the bot to join (defaults to your current voice channel)",
        repeat="Repeat interval (daily, weekdays, weekends, or once)",
    )
    @app_commands.choices(repeat=[
        app_commands.Choice(name="Daily (Every day)", value="daily"),
        app_commands.Choice(name="Weekdays (Monday to Friday)", value="weekdays"),
        app_commands.Choice(name="Weekends (Saturday and Sunday)", value="weekends"),
        app_commands.Choice(name="Once (Play once and finish)", value="once"),
    ])
    async def schedule_add(
        self,
        interaction: discord.Interaction,
        time: str,
        query: str,
        voice_channel: Optional[discord.VoiceChannel] = None,
        repeat: str = "daily",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        locale = await get_locale(guild.id, self.bot.db) if hasattr(self.bot, "db") else "en"
        if not _is_admin(interaction.user):
            await interaction.followup.send(f"❌ {t('error.admin_only', locale)}", ephemeral=True)
            return

        # Voice channel resolution
        target_vc = voice_channel
        if not target_vc and isinstance(interaction.user, discord.Member) and interaction.user.voice:
            target_vc = interaction.user.voice.channel

        if not target_vc:
            await interaction.followup.send(
                "❌ Please specify a `voice_channel` or join a voice channel first.",
                ephemeral=True,
            )
            return

        try:
            task = await self.service.add_task(
                guild_id=guild.id,
                voice_channel_id=target_vc.id,
                text_channel_id=interaction.channel_id or 0,
                user_id=interaction.user.id,
                time_str=time,
                query=query,
                repeat_mode=repeat,
            )
        except ValueError as err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        except Exception as exc:
            logger.error("Failed adding schedule task: %s", exc)
            await interaction.followup.send("❌ Internal database error while creating schedule.", ephemeral=True)
            return

        locale = await get_locale(guild.id, self.bot.db) if hasattr(self.bot, "db") else "en"
        embed = discord.Embed(
            title="⏰ Scheduled Music Alarm",
            description=t("schedule.created", locale, query=task.query, time=task.scheduled_time, repeat=task.days_of_week),
            color=0x2ED573,
        )
        embed.add_field(name="🕒 Time", value=f"`{task.scheduled_time}`", inline=True)
        embed.add_field(name="🔁 Repeat", value=f"`{task.days_of_week.capitalize()}`", inline=True)
        embed.add_field(name="🔊 Voice Channel", value=f"<#{task.voice_channel_id}>", inline=True)
        embed.set_footer(text=f"Task ID #{task.id} · Stored in MySQL scheduled_tasks")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @schedule_group.command(name="list", description="List all scheduled music alarms for this server")
    async def schedule_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        locale = await get_locale(guild.id, self.bot.db) if hasattr(self.bot, "db") else "en"
        if not _is_admin(interaction.user):
            await interaction.followup.send(f"❌ {t('error.admin_only', locale)}", ephemeral=True)
            return
        tasks = await self.service.list_tasks(guild.id)
        if not tasks:
            await interaction.followup.send(t("schedule.empty", locale), ephemeral=True)
            return

        embed = discord.Embed(
            title=t("schedule.list_title", locale, count=len(tasks)),
            color=0x5865F2,
        )
        lines = []
        for task_item in tasks:
            status = "🟢 Active" if task_item.is_active else "⚪ Inactive"
            lines.append(
                f"**#{task_item.id}** `[{task_item.scheduled_time}]` ({task_item.days_of_week.capitalize()}) — **{task_item.query[:35]}**\n"
                f"└ Channel: <#{task_item.voice_channel_id}> · Status: {status}"
            )
        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Use /schedule remove <task_id> to delete an alarm.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @schedule_group.command(name="remove", description="Remove or delete a scheduled music alarm by ID")
    @app_commands.describe(task_id="The ID number of the scheduled task to remove")
    async def schedule_remove(self, interaction: discord.Interaction, task_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        locale = await get_locale(guild.id, self.bot.db) if hasattr(self.bot, "db") else "en"
        if not _is_admin(interaction.user):
            await interaction.followup.send(f"❌ {t('error.admin_only', locale)}", ephemeral=True)
            return
        ok = await self.service.delete_task(guild.id, task_id)
        if ok:
            await interaction.followup.send(t("schedule.deleted", locale, task_id=task_id), ephemeral=True)
        else:
            await interaction.followup.send(t("schedule.not_found", locale, task_id=task_id), ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(SchedulerCog(bot))

