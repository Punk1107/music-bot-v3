# -*- coding: utf-8 -*-
"""
lyrics/cog.py — /lyrics slash command for Music Bot V3 Feature 1.1.

Commands:
  /lyrics          — Show synced lyrics for the currently playing track
  /lyrics sync     — Show lyrics with auto-scroll to current timestamp

UI:
  - Paginated Embed  (10 lines per page)
  - Navigation buttons: ⏮ First | ◀ Prev | ▶ Next | ⏭ Last | 🔄 Jump to Current
  - Highlighted current line: ▶  [MM:SS] **text...**
  - Dim completed lines, highlight upcoming lines normally
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from lyrics.parser import LyricLine, find_current_line_index
from lyrics.service import LyricsService
from utils.embeds import error_embed, info_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

_LINES_PER_PAGE = 12
_TIMEOUT        = 120  # seconds before the View expires


# ── Lyrics Paginator View ───────────────────────────────────────────────────

class LyricsPaginatorView(discord.ui.View):
    """
    Interactive paginator for lyrics embed with 5 navigation buttons.

    Layout:
      Row 0: ⏮ First  |  ◀ Prev  |  ▶ Next  |  ⏭ Last  |  🔄 Sync
    """

    def __init__(
        self,
        lines:      list[LyricLine],
        track_title: str,
        is_auto:    bool,
        player_ref: object,   # weakref-safe: we only call .elapsed_seconds
        bot:        "MusicBot",
        total_pages: int,
        start_page:  int = 0,
    ) -> None:
        super().__init__(timeout=_TIMEOUT)
        self.lines        = lines
        self.track_title  = track_title
        self.is_auto      = is_auto
        self.player_ref   = player_ref
        self.bot          = bot
        self.total_pages  = total_pages
        self.current_page = start_page
        self._update_buttons()

    # ── Build Embed ──────────────────────────────────────────────────────────

    def _build_embed(self) -> discord.Embed:
        """Build the lyrics embed for the current page."""
        start_idx = self.current_page * _LINES_PER_PAGE
        end_idx   = start_idx + _LINES_PER_PAGE
        page_lines = self.lines[start_idx:end_idx]

        elapsed = getattr(self.player_ref, "elapsed_seconds", 0)
        current_idx = find_current_line_index(self.lines, elapsed)

        lines_text: list[str] = []
        for local_i, line in enumerate(page_lines):
            global_i = start_idx + local_i
            ts       = f"`[{line.timestamp_str}]`"

            if global_i == current_idx:
                # Currently playing line — highlighted with arrow and bold
                lines_text.append(f"▶  {ts} **{discord.utils.escape_markdown(line.text)}**")
            elif global_i < current_idx:
                # Already played — dim with strikethrough look (italic)
                lines_text.append(f"   {ts} ~~{discord.utils.escape_markdown(line.text)}~~")
            else:
                # Upcoming
                lines_text.append(f"   {ts} {discord.utils.escape_markdown(line.text)}")

        description = "\n".join(lines_text) or "*No lyrics on this page.*"

        sub_type = "Auto-generated captions" if self.is_auto else "Subtitles"
        embed = discord.Embed(
            title       = f"🎵 Lyrics — {self.track_title[:60]}",
            description = description,
            color       = 0x5865F2,
        )
        embed.set_footer(
            text=f"Page {self.current_page + 1}/{self.total_pages}  •  {sub_type}  •  {len(self.lines)} lines total"
        )
        return embed

    def _update_buttons(self) -> None:
        """Enable/disable navigation buttons based on current page."""
        self.btn_first.disabled = self.current_page == 0
        self.btn_prev.disabled  = self.current_page == 0
        self.btn_next.disabled  = self.current_page >= self.total_pages - 1
        self.btn_last.disabled  = self.current_page >= self.total_pages - 1

    # ── Buttons ──────────────────────────────────────────────────────────────

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, custom_id="ly_first")
    async def btn_first(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="ly_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="ly_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, custom_id="ly_last")
    async def btn_last(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_page = self.total_pages - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="🔄 Current", style=discord.ButtonStyle.primary, custom_id="ly_sync")
    async def btn_sync(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Jump to the page that contains the currently playing line."""
        elapsed     = getattr(self.player_ref, "elapsed_seconds", 0)
        current_idx = find_current_line_index(self.lines, elapsed)
        self.current_page = current_idx // _LINES_PER_PAGE
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def on_timeout(self) -> None:
        """Disable all buttons when the view expires."""
        for child in self.children:
            child.disabled = True


# ── Lyrics Cog ──────────────────────────────────────────────────────────────

class LyricsCog(commands.Cog, name="Lyrics"):
    """Feature 1.1 — Show synced lyrics from YouTube subtitles."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot     = bot
        self.service = LyricsService()

    @app_commands.command(
        name        = "lyrics",
        description = "Show synced lyrics for the currently playing track (from YouTube subtitles)",
    )
    @app_commands.describe(sync="Jump straight to the current timestamp (default: True)")
    async def lyrics(
        self,
        interaction: discord.Interaction,
        sync: bool = True,
    ) -> None:
        await interaction.response.defer(thinking=True)

        player = self.bot.get_player(interaction.guild_id)

        if not player.now_playing:
            await interaction.followup.send(
                embed=error_embed("Nothing Playing", "There is no track currently playing."),
                ephemeral=True,
            )
            return

        track = player.now_playing

        # Only YouTube URLs have subtitles supported via this method
        # SoundCloud / Bandcamp may have them in future but currently not supported
        video_url = track.url
        if not any(domain in video_url for domain in ("youtube.com", "youtu.be")):
            await interaction.followup.send(
                embed=error_embed(
                    "Not Supported",
                    "Lyrics via subtitles are only available for YouTube tracks.\n"
                    "SoundCloud and Bandcamp do not provide subtitle streams.",
                ),
                ephemeral=True,
            )
            return

        # Fetch lyrics (may take a few seconds)
        try:
            result = await asyncio.wait_for(
                self.service.get_lyrics(video_url, self.bot.http_session),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Timed Out", "Could not fetch subtitles in time. Please try again."),
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.error("Lyrics fetch error for '%s': %s", track.title[:50], exc)
            await interaction.followup.send(
                embed=error_embed("Error", "An unexpected error occurred while fetching lyrics."),
                ephemeral=True,
            )
            return

        if result is None:
            await interaction.followup.send(
                embed=info_embed(
                    "No Lyrics Available",
                    f"**{discord.utils.escape_markdown(track.title)}**\n\n"
                    "This video has no subtitles or auto-generated captions available.\n"
                    "YouTube does not provide lyrics for all videos.",
                ),
            )
            return

        lines, is_auto = result

        if not lines:
            await interaction.followup.send(
                embed=info_embed(
                    "Empty Lyrics",
                    "Subtitles were found but contained no readable text."
                ),
                ephemeral=True,
            )
            return

        total_pages = math.ceil(len(lines) / _LINES_PER_PAGE)

        # Determine start page
        if sync:
            current_idx = find_current_line_index(lines, player.elapsed_seconds)
            start_page  = current_idx // _LINES_PER_PAGE
        else:
            start_page = 0

        view  = LyricsPaginatorView(
            lines       = lines,
            track_title = track.short_title,
            is_auto     = is_auto,
            player_ref  = player,
            bot         = self.bot,
            total_pages = total_pages,
            start_page  = start_page,
        )
        embed = view._build_embed()

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LyricsCog(bot))
