# -*- coding: utf-8 -*-
"""
chapters/cog.py — /chapters and /chapter_jump slash commands for Music Bot V3 Feature 1.2.

Commands:
  /chapters                — List all chapters with an interactive jump Dropdown
  /chapter_jump <time>     — Jump directly to a timestamp (e.g. "3:45" or "225")
  /cjump <time>            — Shorthand alias for /chapter_jump
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from chapters.detector import Chapter, ChapterDetector
from chapters.seek_handler import seek_to_chapter
from chapters.views import ChapterDropdownView, build_chapters_embed
from utils.embeds import error_embed, info_embed
from utils.color_thief import get_dominant_color
from core.i18n import get_locale, t

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# Regex patterns for /jump timestamp argument
_TS_HH_MM_SS = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
_TS_MM_SS    = re.compile(r"^(\d{1,2}):(\d{2})$")
_TS_SECONDS  = re.compile(r"^\d+$")


def _parse_timestamp(arg: str) -> Optional[float]:
    """
    Parse a user-supplied timestamp string into seconds.
    Accepts: "HH:MM:SS", "MM:SS", or plain seconds as an integer string.
    Returns None on parse failure.
    """
    arg = arg.strip()
    m = _TS_HH_MM_SS.match(arg)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = _TS_MM_SS.match(arg)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    if _TS_SECONDS.match(arg):
        return float(arg)
    return None


class ChaptersCog(commands.Cog, name="Chapters"):
    """Feature 1.2 — Chapter detection and seamless jump for long videos."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot      = bot
        self.detector = ChapterDetector()

    # ── /chapters ────────────────────────────────────────────────────────────

    @app_commands.command(
        name        = "chapters",
        description = "Browse and jump to chapters in the currently playing video",
    )
    async def chapters(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Nothing Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        track = player.now_playing

        # Only supported for YouTube
        if not any(d in track.url for d in ("youtube.com", "youtu.be")):
            await interaction.followup.send(
                embed=error_embed(
                    "Not Supported",
                    t("chapters.not_supported", locale),
                ),
                ephemeral=True,
            )
            return

        # Fetch chapters (may take a few seconds)
        try:
            chapters = await asyncio.wait_for(
                self.detector.get_chapters(
                    track.url,
                    fallback_duration=float(track.duration),
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Timed Out", t("chapters.timeout", locale)),
                ephemeral=True,
            )
            return

        if not chapters:
            await interaction.followup.send(
                embed=info_embed(
                    "No Chapters Found",
                    f"**{discord.utils.escape_markdown(track.title)}**\n\n{t('chapters.none_found', locale)}",
                ),
            )
            return

        color = await get_dominant_color(track.thumbnail, self.bot.http_session)
        embed = build_chapters_embed(
            chapters    = chapters,
            track_title = track.short_title,
            current_sec = float(player.elapsed_seconds),
            color       = color,
        )
        view = ChapterDropdownView(
            bot      = self.bot,
            guild_id = interaction.guild_id,
            chapters = chapters,
        )
        await interaction.followup.send(embed=embed, view=view)

    # ── /chapter_jump & /cjump ────────────────────────────────────────────────

    @app_commands.command(
        name        = "chapter_jump",
        description = "Jump to a specific timestamp in the current track (e.g. 3:45 or 225)",
    )
    @app_commands.describe(timestamp="Timestamp to jump to: HH:MM:SS, MM:SS, or seconds (e.g. 3:45)")
    async def chapter_jump(self, interaction: discord.Interaction, timestamp: str) -> None:
        await self._do_jump(interaction, timestamp)

    @app_commands.command(
        name        = "cjump",
        description = "Quick shortcut to jump to a specific timestamp in the current track",
    )
    @app_commands.describe(timestamp="Timestamp to jump to: HH:MM:SS, MM:SS, or seconds (e.g. 3:45)")
    async def cjump(self, interaction: discord.Interaction, timestamp: str) -> None:
        await self._do_jump(interaction, timestamp)

    async def _check_permissions(self, interaction: discord.Interaction, locale: str) -> bool:
        """Verify the user is connected to voice in the correct channel and has DJ/Admin permissions."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False

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

        await interaction.followup.send(
            embed=error_embed("DJ Role Required", t("error.dj_required", locale)),
            ephemeral=True,
        )
        return False

    async def _do_jump(self, interaction: discord.Interaction, timestamp: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        locale = await get_locale(interaction.guild_id, self.bot.db)

        if not await self._check_permissions(interaction, locale):
            return

        player = self.bot.get_player(interaction.guild_id)

        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Nothing Playing", t("error.not_playing", locale)),
                ephemeral=True,
            )
            return

        target_sec = _parse_timestamp(timestamp)
        if target_sec is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Invalid Timestamp",
                    t("chapters.invalid_ts", locale),
                ),
                ephemeral=True,
            )
            return

        track = player.now_playing

        # Clamp to track duration
        if track.duration > 0 and target_sec >= track.duration:
            await interaction.followup.send(
                embed=error_embed(
                    "Out of Range",
                    t("chapters.out_of_range", locale, duration=track.duration_str),
                ),
                ephemeral=True,
            )
            return

        if target_sec < 0:
            target_sec = 0.0

        # Build a synthetic Chapter for the seek handler
        from chapters.detector import _format_ts
        dummy_chapter = Chapter(
            index        = 0,
            title        = f"Jump to {_format_ts(target_sec)}",
            start_sec    = target_sec,
            end_sec      = float(track.duration),
            duration_sec = max(0.0, float(track.duration) - target_sec),
        )

        success = await seek_to_chapter(self.bot, interaction.guild_id, dummy_chapter)

        if success:
            embed = discord.Embed(
                title       = t("chapters.jumped", locale, time=_format_ts(target_sec)),
                description = f"Now playing: **{discord.utils.escape_markdown(track.title)}**\n"
                              f"Seeked to: `{_format_ts(target_sec)}`",
                color       = 0x2ED573,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Seek Failed", t("chapters.seek_failed", locale)),
                ephemeral=True,
            )


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(ChaptersCog(bot))
