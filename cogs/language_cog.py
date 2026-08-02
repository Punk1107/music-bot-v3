# -*- coding: utf-8 -*-
"""
cogs/language_cog.py — Localization (Feature 30) for Music Bot V3.

Allows per-guild language switching between English and Thai.
All bot messages will use the selected language after this is set.

Commands:
  /language <en|th> — Set the UI language for this server
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import t, set_locale_cache, supported_locales
from utils.embeds import error_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

_LOCALE_META = {
    "en": {
        "name":  "🇬🇧 English",
        "desc":  "All bot responses will be in English.",
        "color": 0x5865F2,
    },
    "th": {
        "name":  "🇹🇭 ภาษาไทย",
        "desc":  "บอทจะตอบสนองเป็นภาษาไทยทั้งหมด",
        "color": 0xA8385D,
    },
}


class LanguageCog(commands.Cog, name="Language"):
    """Language / Localization commands (F30)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        # Also allow DJ role
        try:
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
            if cfg.dj_role_id and any(r.id == cfg.dj_role_id for r in interaction.user.roles):
                return True
        except Exception:
            pass
        await interaction.followup.send(
            embed=error_embed(
                "Permission Denied",
                "Only **Administrators** or **DJs** can change the server language.",
            ),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="language", description="Set the UI language for this server (EN/TH)")
    @app_commands.describe(locale="Language to switch to")
    @app_commands.choices(locale=[
        app_commands.Choice(name="🇬🇧 English",      value="en"),
        app_commands.Choice(name="🇹🇭 ภาษาไทย (Thai)", value="th"),
    ])
    async def language(self, interaction: discord.Interaction, locale: str) -> None:
        await interaction.response.defer(ephemeral=False)
        if not await self._check_admin(interaction):
            return

        if locale not in supported_locales():
            await interaction.followup.send(
                embed=error_embed("Invalid Locale", f"Supported: {', '.join(supported_locales())}"),
                ephemeral=True,
            )
            return

        # Save to DB config
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.language = locale
        await self.bot.db.save_server_config(cfg)

        # Update in-memory cache
        set_locale_cache(interaction.guild_id, locale)

        meta  = _LOCALE_META.get(locale, _LOCALE_META["en"])
        embed = discord.Embed(
            title       = f"🌐  Language Changed: {meta['name']}",
            description = (
                f"{meta['desc']}\n\n"
                f"Use `/language` again to switch.\n"
                f"*This setting is saved and persists across restarts.*"
            ),
            color       = meta["color"],
        )
        embed.set_footer(text=f"Guild: {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="languageinfo", description="Show the current UI language for this server")
    async def languageinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        cfg    = await self.bot.db.get_server_config(interaction.guild_id)
        locale = getattr(cfg, "language", "en")
        meta   = _LOCALE_META.get(locale, _LOCALE_META["en"])

        embed = discord.Embed(
            title       = "🌐  Current Language",
            description = (
                f"**{meta['name']}**\n"
                f"{meta['desc']}\n\n"
                f"Use `/language` to change."
            ),
            color       = meta["color"],
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LanguageCog(bot))
