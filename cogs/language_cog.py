# -*- coding: utf-8 -*-
"""
cogs/language_cog.py — Localization (Feature 30) for Music Bot V3.

Allows per-guild language switching across 9 supported languages.
All bot messages will use the selected language after this is set.

Commands:
  /language <locale>  — Set the UI language for this server (Admin/DJ only)
  /languageinfo       — Show the current UI language for this server

Supported locales: en, th, zh_cn, ja, ko, es, ru, fr, de
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import (
    t,
    get_locale,
    set_locale_cache,
    supported_locales,
    get_locale_meta,
)
from utils.embeds import error_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# ── Discord only supports up to 25 choices per slash-command option ──────────
# We have exactly 9 languages — well within limit.

_LANGUAGE_CHOICES = [
    app_commands.Choice(name="🇬🇧 English",                      value="en"),
    app_commands.Choice(name="🇹🇭 ภาษาไทย (Thai)",               value="th"),
    app_commands.Choice(name="🇨🇳 简体中文 (Chinese Simplified)",  value="zh_cn"),
    app_commands.Choice(name="🇯🇵 日本語 (Japanese)",              value="ja"),
    app_commands.Choice(name="🇰🇷 한국어 (Korean)",                value="ko"),
    app_commands.Choice(name="🇪🇸 Español (Spanish)",             value="es"),
    app_commands.Choice(name="🇷🇺 Русский (Russian)",             value="ru"),
    app_commands.Choice(name="🇫🇷 Français (French)",             value="fr"),
    app_commands.Choice(name="🇩🇪 Deutsch (German)",              value="de"),
]


class LanguageCog(commands.Cog, name="Language"):
    """Language / Localization commands (F30)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── Permission check ──────────────────────────────────────────────────────

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        """Return True if the user is an Administrator or has the DJ role."""
        if interaction.user.guild_permissions.administrator:
            return True
        try:
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
            if cfg.dj_role_id and any(
                r.id == cfg.dj_role_id for r in interaction.user.roles
            ):
                return True
        except Exception:
            pass

        # Retrieve current locale for the permission-denied message
        try:
            current_locale = await get_locale(interaction.guild_id, self.bot.db)
        except Exception:
            current_locale = "en"

        await interaction.followup.send(
            embed=error_embed(
                "Permission Denied",
                t("lang.permission_denied", current_locale),
            ),
            ephemeral=True,
        )
        return False

    # ── /language ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="language",
        description="Set the UI language for this server (Admin/DJ only)",
    )
    @app_commands.describe(locale="Language to switch to")
    @app_commands.choices(locale=_LANGUAGE_CHOICES)
    async def language(self, interaction: discord.Interaction, locale: str) -> None:
        await interaction.response.defer(ephemeral=False)

        if not await self._check_admin(interaction):
            return

        if locale not in supported_locales():
            await interaction.followup.send(
                embed=error_embed(
                    "Invalid Locale",
                    f"Supported: `{', '.join(sorted(supported_locales()))}`",
                ),
                ephemeral=True,
            )
            return

        # Persist to DB
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.language = locale
        await self.bot.db.save_server_config(cfg)

        # Update in-memory cache
        set_locale_cache(interaction.guild_id, locale)

        meta = get_locale_meta(locale)

        embed = discord.Embed(
            title=(
                f"{meta['flag']}  Language Changed: {meta['native_name']}"
            ),
            description=(
                f"{t('lang.set', locale)}\n\n"
                f"Use `/language` again to switch.\n"
                f"*This setting is saved and persists across restarts.*"
            ),
            color=meta["color"],
        )
        embed.add_field(
            name="Language",
            value=f"{meta['flag']} {meta['native_name']} ({meta['name']})",
            inline=True,
        )
        embed.add_field(
            name="Locale Code",
            value=f"`{locale}`",
            inline=True,
        )
        embed.set_footer(text=f"Guild: {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /languageinfo ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="languageinfo",
        description="Show the current UI language for this server",
    )
    async def languageinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        cfg    = await self.bot.db.get_server_config(interaction.guild_id)
        locale = getattr(cfg, "language", "en")
        meta   = get_locale_meta(locale)

        # Build a list of all supported locales for the embed
        all_locales_str = "\n".join(
            f"{'▶ ' if lc == locale else '   '}`{lc}` — {get_locale_meta(lc)['flag']} {get_locale_meta(lc)['native_name']}"
            for lc in sorted(supported_locales())
        )

        embed = discord.Embed(
            title="🌐  Current Language",
            description=(
                f"**{meta['flag']} {meta['native_name']}** ({meta['name']})\n"
                f"Locale code: `{locale}`\n\n"
                f"Use `/language` to change."
            ),
            color=meta["color"],
        )
        embed.add_field(
            name="All Supported Languages",
            value=all_locales_str,
            inline=False,
        )
        embed.set_footer(text=f"Guild: {interaction.guild.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(LanguageCog(bot))
