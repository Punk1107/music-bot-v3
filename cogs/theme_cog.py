# -*- coding: utf-8 -*-
"""
cogs/theme_cog.py — Theme System (Feature 27) for Music Bot V3.

Themes control the visual appearance of now-playing and queue embeds.

Available themes:
  classic — Dynamic colour from track thumbnail (current behaviour)
  spotify — Spotify green, large artwork, minimal text
  minimal — Dark background, stripped-down layout
  glass   — Frosted / gradient pastel feel

Commands:
  /theme <classic|spotify|minimal|glass> — Set the embed theme for this guild
  /themeinfo                             — Preview all themes
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from models.enums import EmbedTheme
from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


# ── Theme Preview Embeds ──────────────────────────────────────────────────────

def _theme_preview_embed(theme: EmbedTheme) -> discord.Embed:
    """Build a demonstration embed for each theme."""
    sample_title  = "Never Gonna Give You Up"
    sample_artist = "Rick Astley"
    sample_bar    = "▓▓▓▓▓▓▓▓░░░░░░░  3:20 / 3:33"

    if theme == EmbedTheme.CLASSIC:
        embed = discord.Embed(
            title       = f"🎵  {sample_title}",
            description = f"by **{sample_artist}**\n\n`{sample_bar}`",
            color       = 0x5865F2,
        )
        embed.add_field(name="👤 Requested by", value="User#1234",   inline=True)
        embed.add_field(name="🔁 Loop",         value="Queue",       inline=True)
        embed.add_field(name="🔊 Volume",        value="100%",        inline=True)
        embed.set_thumbnail(url="https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg")
        embed.set_footer(text="🎨 Classic Theme — colour adapts to track thumbnail")

    elif theme == EmbedTheme.SPOTIFY:
        embed = discord.Embed(
            title       = sample_title,
            description = (
                f"**{sample_artist}**\n\n"
                f"```{sample_bar}```"
            ),
            color       = EmbedTheme.SPOTIFY.accent_color(),
        )
        embed.set_image(url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg")
        embed.set_footer(text="🟢 Spotify Theme")

    elif theme == EmbedTheme.MINIMAL:
        embed = discord.Embed(
            description = (
                f"**{sample_title}** — {sample_artist}\n"
                f"`{sample_bar}`"
            ),
            color       = EmbedTheme.MINIMAL.accent_color(),
        )
        embed.set_footer(text="◻️ Minimal Theme")

    elif theme == EmbedTheme.GLASS:
        embed = discord.Embed(
            title       = f"♪  {sample_title}",
            description = (
                f"*{sample_artist}*\n\n"
                f"```{sample_bar}```\n"
                "✨ *Frosted Glass*"
            ),
            color       = EmbedTheme.GLASS.accent_color(),
        )
        embed.set_thumbnail(url="https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg")
        embed.set_footer(text="🔮 Glass Theme")

    else:
        embed = discord.Embed(title="Unknown Theme", color=0x2F3136)

    return embed


# ── Cog ──────────────────────────────────────────────────────────────────────

class ThemeCog(commands.Cog, name="Theme"):
    """Theme System commands."""

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

    @app_commands.command(name="theme", description="Set the embed visual theme for this server")
    @app_commands.describe(style="Theme style to use")
    @app_commands.choices(style=[
        app_commands.Choice(name="🎨 Classic  — Dynamic colour from thumbnail",  value="classic"),
        app_commands.Choice(name="🟢 Spotify  — Green, large artwork card",       value="spotify"),
        app_commands.Choice(name="◻️ Minimal  — Dark, ultra-clean, no fields",    value="minimal"),
        app_commands.Choice(name="🔮 Glass    — Frosted / gradient pastel feel",  value="glass"),
    ])
    async def theme(self, interaction: discord.Interaction, style: str) -> None:
        await interaction.response.defer(ephemeral=False)  # visible so the preview is seen by all
        if not await self._check_dj(interaction):
            return

        try:
            chosen = EmbedTheme(style)
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Invalid Theme", "Choose: classic, spotify, minimal, or glass."),
                ephemeral=True,
            )
            return

        # Save to server config
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.embed_theme = chosen
        await self.bot.db.save_server_config(cfg)

        # Apply to active player
        player = self.bot.get_player(interaction.guild_id)
        player.embed_theme = chosen.value

        # Build response with live preview
        preview = _theme_preview_embed(chosen)
        confirm = discord.Embed(
            title       = f"✅  Theme Changed: {chosen.display_name()}",
            description = "Now-playing and queue embeds will use this theme.\n*Preview below:*",
            color       = chosen.accent_color(0x5865F2),
        )

        await interaction.followup.send(embeds=[confirm, preview])

    @app_commands.command(name="themeinfo", description="Preview all available embed themes")
    async def themeinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        cfg    = await self.bot.db.get_server_config(interaction.guild_id)
        active = cfg.embed_theme

        embeds = []
        for t in EmbedTheme:
            preview = _theme_preview_embed(t)
            marker  = " ← active" if t == active else ""
            preview.set_footer(text=f"{t.display_name()}{marker}")
            embeds.append(preview)

        # Discord allows up to 10 embeds per message
        await interaction.followup.send(embeds=embeds[:4], ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(ThemeCog(bot))
