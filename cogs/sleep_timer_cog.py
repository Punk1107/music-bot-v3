# -*- coding: utf-8 -*-
"""
cogs/sleep_timer_cog.py — Sleep Timer (Feature 16) for Music Bot V3.

Commands:
  /sleep <duration>  — Set a sleep timer (e.g. "20m", "1h", "90s", "off")
  /sleepstatus       — Show remaining time on the active timer
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, success_embed, info_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# ── Duration parser ───────────────────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?",
    re.IGNORECASE,
)


def _parse_duration(text: str) -> int | None:
    """
    Parse a human duration string into total seconds.
    Accepts: "20m", "1h30m", "90s", "1h 30m 20s", etc.
    Returns None if the string is not a valid duration.
    """
    text = text.strip().lower()
    if text in ("off", "cancel", "stop"):
        return 0  # special: cancel

    m = _DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        return None

    hours   = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    total   = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _format_remaining(seconds: int) -> str:
    """Format seconds into 'Xh Ym Zs' display string."""
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


# ── Embeds ────────────────────────────────────────────────────────────────────

def _sleep_set_embed(seconds: int, end_time: datetime) -> discord.Embed:
    remaining = _format_remaining(seconds)
    ts = int(end_time.timestamp())
    embed = discord.Embed(
        title       = "😴  Sleep Timer Set",
        description = (
            f"Bot will stop playback in **{remaining}**.\n"
            f"Fires at <t:{ts}:T> (<t:{ts}:R>)"
        ),
        color       = 0x5865F2,
    )
    embed.set_footer(text="Use /sleep off to cancel")
    return embed


def _sleep_status_embed(seconds: int, end_time: datetime) -> discord.Embed:
    remaining = _format_remaining(seconds)
    ts = int(end_time.timestamp())
    embed = discord.Embed(
        title       = "⏱  Sleep Timer Status",
        description = (
            f"**{remaining}** remaining\n"
            f"Will stop at <t:{ts}:T> (<t:{ts}:R>)"
        ),
        color       = 0x70A1FF,
    )
    return embed


def _sleep_fired_embed() -> discord.Embed:
    return discord.Embed(
        title       = "😴  Sleep Timer Fired",
        description = "Playback has been stopped as scheduled. おやすみ 🌙",
        color       = 0x2F3136,
    )


# ── Cog ──────────────────────────────────────────────────────────────────────

class SleepTimerCog(commands.Cog, name="SleepTimer"):
    """Sleep Timer commands."""

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
        from utils.error_handler import dj_required_embed
        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    @app_commands.command(
        name        = "sleep",
        description = 'Set a sleep timer — bot stops after duration (e.g. "20m", "1h", "off")',
    )
    @app_commands.describe(duration='Duration like "20m", "1h30m", "90s" — or "off" to cancel')
    async def sleep(self, interaction: discord.Interaction, duration: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)

        # Cancel case
        lower = duration.strip().lower()
        if lower in ("off", "cancel", "stop", "0"):
            was_active = player.cancel_sleep_timer()
            if was_active:
                await interaction.followup.send(
                    embed=success_embed("Sleep Timer Cancelled", "Timer has been cleared."),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=info_embed("No Timer Active", "There is no sleep timer running."),
                    ephemeral=True,
                )
            return

        total_secs = _parse_duration(duration)
        if total_secs is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Invalid Duration",
                    'Use formats like `20m`, `1h`, `90s`, `1h30m`, or `off` to cancel.',
                ),
                ephemeral=True,
            )
            return

        # Cap at 24 hours
        total_secs = min(total_secs, 86400)

        # Cancel any existing timer first
        player.cancel_sleep_timer()

        end_time           = datetime.now(timezone.utc) + timedelta(seconds=total_secs)
        player.sleep_timer_end = end_time

        guild_id = interaction.guild_id

        async def _timer_task() -> None:
            try:
                await asyncio.sleep(total_secs)
                _player = self.bot.get_player(guild_id)
                _guild  = self.bot.get_guild(guild_id)
                if not _guild:
                    return
                _vc = _guild.voice_client
                if _vc and (_vc.is_playing() or _vc.is_paused()):
                    _vc.stop()
                _player.reset()
                _player.intentional_disconnect = True
                if _vc and _vc.is_connected():
                    await _vc.disconnect(force=True)
                # Notify
                if _player.text_channel:
                    try:
                        await _player.text_channel.send(embed=_sleep_fired_embed())
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass
            finally:
                _p = self.bot.get_player(guild_id)
                _p.sleep_timer_task = None
                _p.sleep_timer_end  = None

        player.sleep_timer_task = asyncio.create_task(_timer_task())

        await interaction.followup.send(
            embed=_sleep_set_embed(total_secs, end_time),
            ephemeral=True,
        )

    @app_commands.command(name="sleepstatus", description="Show remaining time on the sleep timer")
    async def sleepstatus(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.bot.get_player(interaction.guild_id)
        remaining = player.sleep_remaining_seconds()
        if remaining <= 0 or not player.sleep_timer_end:
            await interaction.followup.send(
                embed=info_embed("No Timer Active", "Start one with `/sleep <duration>`."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=_sleep_status_embed(remaining, player.sleep_timer_end),
            ephemeral=True,
        )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(SleepTimerCog(bot))
