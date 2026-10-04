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

_ERROR_CLASSIFICATIONS = [
    (["copyright", "has been blocked"], "Copyright Restriction", "⚖️",
     "This track has been blocked due to copyright restrictions.",
     "เพลงนี้ถูกบล็อกเนื่องจากลิขสิทธิ์"),
    (["age-restricted", "age restricted", "sign in to confirm your age"], "Age-Restricted Content", "🔞",
     "This content is age-restricted and cannot be played.",
     "เนื้อหานี้จำกัดอายุและไม่สามารถเล่นได้"),
    (["private video", "video is private"], "Private Video", "🔒",
     "This video is private and cannot be accessed.",
     "วิดีโอนี้เป็นส่วนตัวและไม่สามารถเข้าถึงได้"),
    (["video unavailable", "removed by the user"], "Video Unavailable", "❌",
     "This video is no longer available.",
     "วิดีโอนี้ไม่สามารถใช้งานได้อีกต่อไป"),
    (["rate limit", "429", "too many requests"], "Rate Limited", "⏳",
     "YouTube is rate-limiting requests. Please try again in a few minutes.",
     "YouTube จำกัดคำขอ กรุณาลองอีกครั้งในไม่กี่นาที"),
    (["network", "connection", "timeout"], "Network Error", "🌐",
     "A network error occurred. Please check your connection.",
     "เกิดข้อผิดพลาดเครือข่าย กรุณาตรวจสอบการเชื่อมต่อ"),
]

_FALLBACK = ("Playback Error", "⚠️",
             "An unknown error occurred during playback.",
             "เกิดข้อผิดพลาดที่ไม่ทราบสาเหตุระหว่างการเล่นเพลง")


def classify_error(error_str: str) -> tuple[str, str, str, str]:
    """
    Returns (title, emoji, description_en, description_th).
    """
    err_lower = error_str.lower()
    for keywords, title, emoji, desc_en, desc_th in _ERROR_CLASSIFICATIONS:
        if any(k in err_lower for k in keywords):
            return title, emoji, desc_en, desc_th
    return _FALLBACK


# ── Embed builders ────────────────────────────────────────────────────────────

def command_error_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title       = f"❌ {title}",
        description = description,
        color       = discord.Color.red(),
    )
    return embed


def playback_error_embed(error_str: str, locale: str = "en") -> discord.Embed:
    title, emoji, desc_en, desc_th = classify_error(error_str)
    if locale == "th":
        desc = desc_th
    else:
        desc = desc_en
    embed = discord.Embed(
        title       = f"{emoji} {title}",
        description = desc,
        color       = discord.Color.orange(),
    )
    return embed


def voice_connection_error_embed(channel_name: str, attempts: int, locale: str = "en") -> discord.Embed:
    desc = t("error.voice_reconnect_failed", locale, channel=channel_name, attempts=attempts)
    embed = discord.Embed(
        title       = "🔌 Voice Reconnect Failed",
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
    desc = t("error.dj_required", str(locale or "en"))
    return discord.Embed(
        title       = "🎚️ DJ Permission Required",
        description = desc,
        color       = discord.Color.orange(),
    )


def rate_limited_embed(retry_after: float, locale: str = "en") -> discord.Embed:
    desc = t("error.rate_limited", locale, retry_after=f"{retry_after:.1f}")
    return discord.Embed(
        title       = "⏳ Slow Down!",
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
