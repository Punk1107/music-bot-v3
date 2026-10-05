# -*- coding: utf-8 -*-
"""
utils/error_handler.py — Bilingual error classification & embed builders for V3.

Errors display with English + Thai subtitles.
Classifies common playback failures (copyright, age-restricted, rate limit, etc.)

V3 Stability additions:
  - ExceptionKind re-export (from core.stability)
  - log_classified(): log exception at correct level based on its kind
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional, TYPE_CHECKING, Any

import discord

# Re-export for callers that import from utils.error_handler
from core.stability import ExceptionKind, log_with_kind as log_classified  # noqa: F401
from core.i18n import get_locale, t

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


# ── Error classification ──────────────────────────────────────────────────────

# ── Error classification ──────────────────────────────────────────────────────

_ERROR_CATEGORIES = [
    (["copyright", "has been blocked"], "copyright", "⚖️"),
    (["age-restricted", "age restricted", "sign in to confirm your age"], "age_restricted", "🔞"),
    (["private video", "video is private"], "private_video", "🔒"),
    (["video unavailable", "unavailable", "is unavailable", "removed by the user"], "video_unavailable", "❌"),
    (["rate limit", "429", "too many requests"], "rate_limited_yt", "⏳"),
    (["network", "connection", "timeout"], "network", "🌐"),
]

_FALLBACK_CATEGORY = ("playback_fallback", "⚠️")


def get_error_category(error_str: str) -> tuple[str, str]:
    """Returns (category_key, emoji)."""
    err_lower = error_str.lower()
    for keywords, category, emoji in _ERROR_CATEGORIES:
        if any(k in err_lower for k in keywords):
            return category, emoji
    return _FALLBACK_CATEGORY


def classify_error(error_str: str) -> tuple[str, str, str, str]:
    """
    Returns (title, emoji, description_en, description_th).
    Maintained for backward compatibility.
    """
    category, emoji = get_error_category(error_str)
    title = t(f"error.{category}.title", "en")
    desc_en = t(f"error.{category}.desc", "en")
    desc_th = t(f"error.{category}.desc", "th")
    return title, emoji, desc_en, desc_th


# ── Embed builders ────────────────────────────────────────────────────────────

def command_error_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title       = f"❌ {title}",
        description = description,
        color       = discord.Color.red(),
    )
    return embed


def playback_error_embed(error_str: str, locale: str = "en") -> discord.Embed:
    category, emoji = get_error_category(error_str)
    title = t(f"error.{category}.title", locale)
    desc = t(f"error.{category}.desc", locale)
    embed = discord.Embed(
        title       = f"{emoji} {title}",
        description = desc,
        color       = discord.Color.orange(),
    )
    return embed


def voice_connection_error_embed(channel_name: str, attempts: int, locale: str = "en") -> discord.Embed:
    desc = t("error.voice_reconnect_failed", locale, channel=channel_name, attempts=attempts)
    title = t("error.voice_reconnect_title", locale)
    embed = discord.Embed(
        title       = title,
        description = desc,
        color       = discord.Color.red(),
    )
    return embed


def dj_required_embed(locale: Any = "en") -> discord.Embed:
    from core.i18n import get_locale_sync
    if hasattr(locale, "guild_id") and not isinstance(locale, str):
        locale = get_locale_sync(getattr(locale, "guild_id", None))
    elif hasattr(locale, "guild") and not isinstance(locale, str):
        g = getattr(locale, "guild", None)
        locale = get_locale_sync(getattr(g, "id", None) if g else None)
    elif isinstance(locale, int):
        locale = get_locale_sync(locale)
    loc_str = str(locale or "en")
    desc = t("error.dj_required", loc_str)
    title = t("error.dj_required_title", loc_str)
    return discord.Embed(
        title       = title,
        description = desc,
        color       = discord.Color.orange(),
    )


def rate_limited_embed(retry_after: float, locale: str = "en") -> discord.Embed:
    desc = t("error.rate_limited", locale, retry_after=f"{retry_after:.1f}")
    title = t("error.rate_limit_title", locale)
    return discord.Embed(
        title       = title,
        description = desc,
        color       = discord.Color.yellow(),
    )


async def notify_playback_error(
    bot:      "MusicBot",
    guild_id: int,
    track_title: str,
    error:    Exception,
) -> None:
    """Send a playback error to the guild's text channel (if known)."""
    player = bot.get_player(guild_id)
    channel = player.text_channel
    if not channel:
        return
    try:
        locale = await get_locale(guild_id, bot.db)
        embed = playback_error_embed(str(error), locale=locale)
        embed.set_footer(text=f"Track: {track_title[:80]}")
        await channel.send(embed=embed, delete_after=30)
    except Exception:
        pass


async def forward_to_dev_channel(
    bot:   "MusicBot",
    error: Exception,
    ctx:   Optional[discord.Interaction] = None,
) -> None:
    """Forward a full traceback to all configured developer log channels."""
    import config as cfg
    channel_ids: list[int] = getattr(cfg, "DEV_LOG_CHANNEL_IDS", [])
    if not channel_ids:
        return

    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    ctx_info = ""
    if ctx:
        ctx_info = f"Guild: {ctx.guild_id} | User: {ctx.user.id} | Command: {ctx.command}\n"
    content = f"```\n{ctx_info}{tb[:1800]}\n```"

    for cid in channel_ids:
        channel = bot.get_channel(cid)
        if not channel:
            logger.debug("forward_to_dev_channel: channel %d not found or not cached.", cid)
            continue
        try:
            await channel.send(content=f"🚨 **Unhandled Error**\n{content}")
        except Exception as send_err:
            logger.debug("forward_to_dev_channel: failed to send to %d: %s", cid, send_err)
