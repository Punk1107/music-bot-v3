# -*- coding: utf-8 -*-
"""
sources/cog.py — SoundCloud & Bandcamp native source commands for Music Bot V3 Feature 1.3.

Commands:
  /scsearch <query>  — Search SoundCloud and pick from results
  /play (extended)   — Existing /play now transparently supports SoundCloud & Bandcamp URLs

Integration:
  The MultiSourceRouter is attached to the bot instance as bot.sources_router.
  The MusicCog's /play command detects SoundCloud / Bandcamp URLs and delegates
  to this module via bot.sources_router.resolve().
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from sources.router import MultiSourceRouter, SourceKind, classify
from utils.embeds import error_embed, success_embed, info_embed, playlist_added_embed
from utils.formatters import truncate, format_duration

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

_TIMEOUT = 60  # seconds for search result View


# ── SoundCloud Search Results View ──────────────────────────────────────────

class SCSearchSelect(discord.ui.Select):
    """Dropdown to pick one track from SoundCloud search results."""

    def __init__(
        self,
        bot:     "MusicBot",
        guild_id: int,
        tracks:  list,
    ) -> None:
        self.bot      = bot
        self.guild_id = guild_id
        self.tracks   = tracks

        options = [
            discord.SelectOption(
                label       = truncate(t.title, 95),
                value       = str(i),
                description = f"{t.uploader} • {t.duration_str}" if t.duration else t.uploader,
                emoji       = "🔊",
            )
            for i, t in enumerate(tracks)
        ]
        super().__init__(
            placeholder = "Choose a SoundCloud track…",
            min_values  = 1,
            max_values  = 1,
            options     = options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        idx   = int(self.values[0])
        track = self.tracks[idx]

        # Ensure user is in a voice channel
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed("Not in Voice", "You must be in a voice channel."),
                ephemeral=True,
            )
            return

        # Add attribution
        track.requested_by_id   = interaction.user.id
        track.requested_by_name = interaction.user.display_name

        # Get or create player
        music_cog = self.bot.cogs.get("Music")
        if not music_cog:
            await interaction.followup.send(
                embed=error_embed("Internal Error", "Music system not available."),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(self.guild_id)
        pos    = await player.enqueue(track)

        embed = discord.Embed(
            title       = "🔊  Added from SoundCloud",
            description = f"**{discord.utils.escape_markdown(track.title)}**\n"
                          f"by *{discord.utils.escape_markdown(track.uploader)}*",
            color       = 0xFF5500,   # SoundCloud orange
        )
        embed.add_field(name="⏱ Duration", value=track.duration_str, inline=True)
        embed.add_field(name="📋 Position", value=f"#{pos}",         inline=True)
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)

        # Disable all items in the parent view
        for child in self.view.children:
            child.disabled = True
        await interaction.message.edit(view=self.view)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Start playback if not already playing
        guild = self.bot.get_guild(self.guild_id)
        vc    = guild.voice_client if guild else None
        if not vc:
            # Try to join the user's channel
            try:
                vc = await member.voice.channel.connect()
                player.last_channel_id = vc.channel.id
                player.text_channel    = interaction.channel
            except Exception as exc:
                logger.error("SourcesCog: voice connect failed: %s", exc)
                return

        if not vc.is_playing() and not vc.is_paused():
            asyncio.create_task(music_cog._play_next(self.guild_id))


class SCSearchView(discord.ui.View):
    """View wrapper for the SoundCloud search select dropdown."""

    def __init__(self, bot: "MusicBot", guild_id: int, tracks: list) -> None:
        super().__init__(timeout=_TIMEOUT)
        self.add_item(SCSearchSelect(bot=bot, guild_id=guild_id, tracks=tracks))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


# ── Sources Cog ──────────────────────────────────────────────────────────────

class SourcesCog(commands.Cog, name="Sources"):
    """Feature 1.3 — SoundCloud & Bandcamp native source support."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot    = bot
        self.router = MultiSourceRouter()
        # Attach router to bot so other cogs (e.g. MusicCog) can reach it
        bot.sources_router = self.router

    # ── /scsearch ────────────────────────────────────────────────────────────

    @app_commands.command(
        name        = "scsearch",
        description = "Search SoundCloud and choose a track to play",
    )
    @app_commands.describe(query="Keywords to search on SoundCloud")
    async def scsearch(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)

        query = query.strip()
        if not query:
            await interaction.followup.send(
                embed=error_embed("Empty Query", "Please provide a search query."),
                ephemeral=True,
            )
            return

        try:
            tracks = await asyncio.wait_for(
                self.router._sc.search(query, max_results=5),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Timed Out", "SoundCloud search timed out. Please try again."),
                ephemeral=True,
            )
            return

        if not tracks:
            await interaction.followup.send(
                embed=info_embed(
                    "No Results",
                    f"No SoundCloud tracks found for **{discord.utils.escape_markdown(query)}**.",
                ),
            )
            return

        embed = discord.Embed(
            title       = f"🔊  SoundCloud Search: {discord.utils.escape_markdown(query[:50])}",
            description = "\n".join(
                f"**{i+1}.** {discord.utils.escape_markdown(t.title)} "
                f"— *{discord.utils.escape_markdown(t.uploader)}* `[{t.duration_str}]`"
                for i, t in enumerate(tracks)
            ),
            color       = 0xFF5500,
        )
        embed.set_footer(text="Select a track from the dropdown below")

        view = SCSearchView(bot=self.bot, guild_id=interaction.guild_id, tracks=tracks)
        await interaction.followup.send(embed=embed, view=view)

    # ── /play integration helper ─────────────────────────────────────────────
    # This method is called by the extended play logic inside SourcesCog.handle_play()
    # and can be invoked from cogs/music.py's /play command after checking router.is_supported()

    async def handle_play(
        self,
        interaction: discord.Interaction,
        query:       str,
        shuffle:     bool = False,
    ) -> bool:
        """
        Handle a /play call for SoundCloud or Bandcamp sources.

        Returns True if this method handled the query (caller should return).
        Returns False if the query is not a supported source (caller should continue).
        """
        if not self.router.is_supported(query):
            return False

        await interaction.response.defer()

        kind = classify(query)

        # Resolve tracks
        max_tracks = 50
        try:
            tracks = await asyncio.wait_for(
                self.router.resolve(query, max_tracks=max_tracks),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Timed Out", "Could not load the track. Please try again."),
                ephemeral=True,
            )
            return True

        if not tracks:
            source_name = "SoundCloud" if "soundcloud" in query.lower() else "Bandcamp"
            await interaction.followup.send(
                embed=error_embed(f"{source_name} Error", "Could not retrieve any tracks from that URL."),
                ephemeral=True,
            )
            return True

        # Ensure voice connection
        music_cog = self.bot.cogs.get("Music")
        if not music_cog:
            await interaction.followup.send(
                embed=error_embed("Internal Error", "Music system not available."),
                ephemeral=True,
            )
            return True

        vc = await music_cog._ensure_voice(interaction)
        if not vc:
            return True

        player = self.bot.get_player(interaction.guild_id)

        # Attribute and enqueue
        for t in tracks:
            t.requested_by_id   = interaction.user.id
            t.requested_by_name = interaction.user.display_name

        if len(tracks) == 1:
            await player.enqueue(tracks[0])
            embed = discord.Embed(
                title       = "🔊  Added to Queue",
                description = f"**{discord.utils.escape_markdown(tracks[0].title)}**\n"
                              f"*{discord.utils.escape_markdown(tracks[0].uploader)}*",
                color       = 0xFF5500 if is_soundcloud_source(kind) else 0x1DA0C3,
            )
            if tracks[0].thumbnail:
                embed.set_thumbnail(url=tracks[0].thumbnail)
            embed.add_field(name="⏱ Duration", value=tracks[0].duration_str, inline=True)
        else:
            if shuffle and len(tracks) > 1:
                random.shuffle(tracks)
            await player.extend(tracks)
            src = "SoundCloud" if is_soundcloud_source(kind) else "Bandcamp"
            embed = playlist_added_embed(len(tracks), shuffled=shuffle)
            embed.title = f"🔊  {src}: Added {len(tracks)} tracks"

        await interaction.followup.send(embed=embed)

        if not vc.is_playing() and not vc.is_paused():
            asyncio.create_task(music_cog._play_next(interaction.guild_id))

        return True


def is_soundcloud_source(kind: SourceKind) -> bool:
    return kind in (SourceKind.SOUNDCLOUD, SourceKind.SOUNDCLOUD_SET, SourceKind.SOUNDCLOUD_SEARCH)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(SourcesCog(bot))
