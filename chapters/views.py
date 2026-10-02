# -*- coding: utf-8 -*-
"""
chapters/views.py — Discord UI components for chapter selection (Feature 1.2).

Provides:
  ChapterDropdownView — a Discord Select menu that lets users pick a chapter
                        and immediately seeks to its start time.

Supports > 25 chapters with page navigation (Discord Select is limited to 25 options).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord

from chapters.detector import Chapter
from chapters.seek_handler import seek_to_chapter
from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

_MAX_SELECT_OPTIONS = 25   # Discord's hard limit per Select component
_TIMEOUT            = 60   # seconds


# ── Single-page Dropdown ─────────────────────────────────────────────────────

class ChapterSelect(discord.ui.Select):
    """A Discord Select menu populated with up to 25 chapter entries."""

    def __init__(self, bot: "MusicBot", guild_id: int, chapters: list[Chapter]) -> None:
        self.bot      = bot
        self.guild_id = guild_id
        self._chapters_map = {str(ch.index): ch for ch in chapters}

        options = [
            discord.SelectOption(
                label       = ch.label[:100],
                value       = str(ch.index),
                description = f"Duration: {ch.duration_str}" if ch.duration_sec > 0 else None,
                emoji       = "🎵",
            )
            for ch in chapters
        ]
        super().__init__(
            placeholder = "Select a chapter to jump to…",
            min_values  = 1,
            max_values  = 1,
            options     = options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        chapter = self._chapters_map.get(self.values[0])
        if not chapter:
            await interaction.followup.send(
                embed=error_embed("Invalid Chapter", "Could not find that chapter."),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(self.guild_id)
        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Nothing Playing", "No track is currently playing."),
                ephemeral=True,
            )
            return

        success = await seek_to_chapter(self.bot, self.guild_id, chapter)
        if success:
            embed = discord.Embed(
                title       = f"⏩  Jumped to Chapter {chapter.index}",
                description = (
                    f"**{discord.utils.escape_markdown(chapter.title)}**\n"
                    f"⏱ Timestamp: `{chapter.start_str}`"
                ),
                color       = 0x2ED573,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Seek Failed", "Could not seek to that chapter. Try again."),
                ephemeral=True,
            )


# ── Multi-page Chapter View ──────────────────────────────────────────────────

class ChapterDropdownView(discord.ui.View):
    """
    Full chapter browser with paginated Dropdown (25 chapters per page).

    Layout:
      Row 0: [Chapter Select Dropdown — up to 25 options]
      Row 1 (if > 25): ◀ Prev Page  |  Page N/M  |  ▶ Next Page
    """

    def __init__(
        self,
        bot:      "MusicBot",
        guild_id: int,
        chapters: list[Chapter],
    ) -> None:
        super().__init__(timeout=_TIMEOUT)
        self.bot       = bot
        self.guild_id  = guild_id
        self.chapters  = chapters
        self.page      = 0
        self.total_pages = max(1, (len(chapters) + _MAX_SELECT_OPTIONS - 1) // _MAX_SELECT_OPTIONS)
        self._rebuild()

    def _current_page_chapters(self) -> list[Chapter]:
        start = self.page * _MAX_SELECT_OPTIONS
        end   = start + _MAX_SELECT_OPTIONS
        return self.chapters[start:end]

    def _rebuild(self) -> None:
        """Rebuild the Select and pagination buttons for the current page."""
        self.clear_items()

        select = ChapterSelect(
            bot      = self.bot,
            guild_id = self.guild_id,
            chapters = self._current_page_chapters(),
        )
        self.add_item(select)

        # Only show pagination buttons if chapters span multiple pages
        if self.total_pages > 1:
            prev_btn = discord.ui.Button(
                label    = "◀ Prev",
                style    = discord.ButtonStyle.secondary,
                disabled = self.page == 0,
                custom_id= "ch_prev",
                row      = 1,
            )
            next_btn = discord.ui.Button(
                label    = f"▶ Next",
                style    = discord.ButtonStyle.secondary,
                disabled = self.page >= self.total_pages - 1,
                custom_id= "ch_next",
                row      = 1,
            )
            page_btn = discord.ui.Button(
                label    = f"Page {self.page + 1}/{self.total_pages}",
                style    = discord.ButtonStyle.primary,
                disabled = True,
                custom_id= "ch_page",
                row      = 1,
            )
            prev_btn.callback = self._prev_page
            next_btn.callback = self._next_page

            self.add_item(prev_btn)
            self.add_item(page_btn)
            self.add_item(next_btn)

    async def _prev_page(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


# ── Chapter List Embed builder ────────────────────────────────────────────────

def build_chapters_embed(
    chapters:    list[Chapter],
    track_title: str,
    current_sec: float = 0.0,
    color:       int   = 0x5865F2,
) -> discord.Embed:
    """
    Build a read-only embed listing the first 20 chapters with
    the currently playing chapter highlighted.
    """
    embed = discord.Embed(
        title = f"📑  Chapters — {track_title[:55]}",
        color = color,
    )

    # Find current chapter
    current_ch_idx = 0
    for i, ch in enumerate(chapters):
        if ch.start_sec <= current_sec:
            current_ch_idx = i

    display = chapters[:20]
    lines: list[str] = []
    for ch in display:
        marker = "▶ " if ch.index - 1 == current_ch_idx else "   "
        line   = f"{marker}**{ch.index}.** `[{ch.start_str}]` {discord.utils.escape_markdown(ch.title)}"
        if ch.index - 1 == current_ch_idx:
            line += "  ← *current*"
        lines.append(line)

    if len(chapters) > 20:
        lines.append(f"*…and {len(chapters) - 20} more — use the dropdown below*")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{len(chapters)} chapters total • Use the dropdown to jump")
    return embed
