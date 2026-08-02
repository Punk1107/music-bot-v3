# -*- coding: utf-8 -*-
"""
core/i18n.py — Localization (i18n) for Music Bot V3.

Tier Performance F30: Full TH/EN localization.

Usage:
    from core.i18n import t, get_locale, set_locale

    locale = await get_locale(guild_id, bot.db)
    msg    = t("now_playing.title", locale)

    # With format params:
    msg = t("queue.added", locale, title="Song", pos=3)

Supported locales:
  "en" — English (default)
  "th" — Thai

All user-visible strings are defined in _STRINGS below.
Fallback: if a key is missing in the target locale, English is used.
If a key is missing in English too, the key itself is returned.

Guild locale is stored in ServerConfig.language (new field).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager

logger = logging.getLogger(__name__)

# ── String catalogue ──────────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    # ── Playback ──────────────────────────────────────────────────────────────
    "now_playing.title": {
        "en": "🎵  Now Playing",
        "th": "🎵  กำลังเล่น",
    },
    "now_playing.footer": {
        "en": "Music Bot V3  •  Now Playing",
        "th": "Music Bot V3  •  กำลังเล่น",
    },
    "now_playing.nothing": {
        "en": "Nothing is playing right now.",
        "th": "ไม่มีเพลงกำลังเล่นอยู่ในขณะนี้",
    },
    "now_playing.paused": {
        "en": "⏸ Paused",
        "th": "⏸ หยุดชั่วคราว",
    },
    "now_playing.playing": {
        "en": "▶ Now Playing",
        "th": "▶ กำลังเล่น",
    },

    # ── Queue ─────────────────────────────────────────────────────────────────
    "queue.added": {
        "en": "Added **{title}** to position #{pos}",
        "th": "เพิ่ม **{title}** ไปที่ตำแหน่ง #{pos} แล้ว",
    },
    "queue.empty": {
        "en": "The queue is empty.",
        "th": "คิวว่างอยู่ค่ะ",
    },
    "queue.cleared": {
        "en": "Queue cleared — removed {count} track(s).",
        "th": "ล้างคิวแล้ว — ลบ {count} เพลง",
    },
    "queue.shuffled": {
        "en": "🔀 Shuffled {count} tracks.",
        "th": "🔀 สับเปลี่ยนลำดับ {count} เพลงแล้ว",
    },
    "queue.removed": {
        "en": "Removed **{title}** from position {pos}.",
        "th": "ลบ **{title}** จากตำแหน่ง {pos} แล้ว",
    },
    "queue.moved": {
        "en": "Track moved from position {from_pos} → {to_pos}.",
        "th": "ย้ายเพลงจากตำแหน่ง {from_pos} → {to_pos} แล้ว",
    },
    "queue.not_enough": {
        "en": "Need at least 2 tracks to shuffle.",
        "th": "ต้องมีอย่างน้อย 2 เพลงในคิวเพื่อสับเปลี่ยน",
    },
    "queue.invalid_pos": {
        "en": "No track at position {pos}.",
        "th": "ไม่มีเพลงที่ตำแหน่ง {pos}",
    },
    "queue.locked": {
        "en": "🔒 The queue is locked. Only DJs can add tracks.",
        "th": "🔒 คิวถูกล็อคอยู่ เฉพาะ DJ เท่านั้นที่เพิ่มเพลงได้",
    },

    # ── Skip ──────────────────────────────────────────────────────────────────
    "skip.done": {
        "en": "⏭ Skipped.",
        "th": "⏭ ข้ามเพลงแล้ว",
    },
    "skip.vote": {
        "en": "Vote to skip: {votes}/{needed} votes",
        "th": "โหวตข้ามเพลง: {votes}/{needed} เสียง",
    },
    "skip.already_voted": {
        "en": "You already voted to skip.",
        "th": "คุณโหวตข้ามไปแล้ว",
    },

    # ── Volume ────────────────────────────────────────────────────────────────
    "volume.set": {
        "en": "🔊 Volume set to **{vol}%**",
        "th": "🔊 ตั้งระดับเสียงเป็น **{vol}%** แล้ว",
    },
    "volume.invalid": {
        "en": "Volume must be between 0 and 200.",
        "th": "ระดับเสียงต้องอยู่ระหว่าง 0 ถึง 200",
    },

    # ── Errors ────────────────────────────────────────────────────────────────
    "error.not_in_voice": {
        "en": "You must be in a voice channel to use this command.",
        "th": "คุณต้องอยู่ในห้องเสียงก่อนใช้คำสั่งนี้",
    },
    "error.not_playing": {
        "en": "Nothing is currently playing.",
        "th": "ไม่มีเพลงกำลังเล่นอยู่ในขณะนี้",
    },
    "error.dj_required": {
        "en": "🎧 You need the DJ role to use this command.",
        "th": "🎧 คุณต้องมีสิทธิ์ DJ เพื่อใช้คำสั่งนี้",
    },
    "error.no_results": {
        "en": "No results found for `{query}`.",
        "th": "ไม่พบผลลัพธ์สำหรับ `{query}`",
    },
    "error.too_long": {
        "en": "Track is too long (max {max}min).",
        "th": "เพลงยาวเกินไป (สูงสุด {max} นาที)",
    },
    "error.circuit_open": {
        "en": "⚠️ YouTube is temporarily unavailable. Please try again shortly.",
        "th": "⚠️ YouTube ไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่ในอีกสักครู่",
    },
    "error.unexpected": {
        "en": "An unexpected error occurred.",
        "th": "เกิดข้อผิดพลาดที่ไม่คาดคิด",
    },
    "error.duplicate": {
        "en": "This track is already in the queue.",
        "th": "เพลงนี้อยู่ในคิวแล้ว",
    },

    # ── Disconnect / Leave ────────────────────────────────────────────────────
    "leave.alone": {
        "en": "👋 Left the channel — no one was listening.",
        "th": "👋 ออกจากห้องเนื่องจากไม่มีคนอยู่ด้วย",
    },
    "leave.idle": {
        "en": "💤 Disconnected due to inactivity.",
        "th": "💤 ออกจากห้องเนื่องจากไม่มีการใช้งาน",
    },
    "leave.manual": {
        "en": "👋 Disconnected.",
        "th": "👋 ตัดการเชื่อมต่อแล้ว",
    },

    # ── Sleep Timer ───────────────────────────────────────────────────────────
    "sleep.set": {
        "en": "😴 Sleep timer set for **{duration}**.",
        "th": "😴 ตั้งตัวจับเวลาหยุดเล่นใน **{duration}**",
    },
    "sleep.cancelled": {
        "en": "Sleep timer cancelled.",
        "th": "ยกเลิกตัวจับเวลาหยุดเล่นแล้ว",
    },
    "sleep.fired": {
        "en": "😴 Playback stopped as scheduled. Good night! 🌙",
        "th": "😴 หยุดเล่นเพลงตามกำหนดแล้ว ราตรีสวัสดิ์ 🌙",
    },
    "sleep.no_timer": {
        "en": "No sleep timer is active.",
        "th": "ไม่มีตัวจับเวลาหยุดเล่นที่ใช้งานอยู่",
    },

    # ── Auto Pause ────────────────────────────────────────────────────────────
    "auto_pause.paused": {
        "en": "⏸ Auto-paused (channel empty).",
        "th": "⏸ หยุดชั่วคราวอัตโนมัติ (ห้องว่าง)",
    },
    "auto_pause.resumed": {
        "en": "▶ Auto-resumed (someone joined).",
        "th": "▶ เล่นต่ออัตโนมัติ (มีคนเข้าห้อง)",
    },

    # ── Undo / Transaction ────────────────────────────────────────────────────
    "undo.success": {
        "en": "↩️ Undid **{op}** — restored {count} track(s).",
        "th": "↩️ เลิกทำ **{op}** — คืนค่า {count} เพลงแล้ว",
    },
    "undo.empty": {
        "en": "Nothing to undo.",
        "th": "ไม่มีอะไรให้เลิกทำ",
    },

    # ── Effects ───────────────────────────────────────────────────────────────
    "effects.applied": {
        "en": "🎛 Effect applied: **{effect}**",
        "th": "🎛 ใช้เอฟเฟกต์: **{effect}** แล้ว",
    },
    "effects.cleared": {
        "en": "🎛 All effects cleared.",
        "th": "🎛 ล้างเอฟเฟกต์ทั้งหมดแล้ว",
    },

    # ── Playback Controls (A+) ────────────────────────────────────────────────
    "speed.set": {
        "en": "⚡ Speed set to **{rate}x**",
        "th": "⚡ ตั้งความเร็วเป็น **{rate}x** แล้ว",
    },
    "pitch.set": {
        "en": "🎵 Pitch set to **{semitones:+d} semitone(s)**",
        "th": "🎵 ตั้ง Pitch เป็น **{semitones:+d} เซมิโทน** แล้ว",
    },
    "crossfade.set": {
        "en": "🎚️ Crossfade set to **{secs}s**",
        "th": "🎚️ ตั้ง Crossfade เป็น **{secs}s** แล้ว",
    },
    "crossfade.off": {
        "en": "🎚️ Crossfade disabled.",
        "th": "🎚️ ปิด Crossfade แล้ว",
    },

    # ── Presets ───────────────────────────────────────────────────────────────
    "preset.applied": {
        "en": "✅ Preset **{name}** applied.",
        "th": "✅ ใช้งาน Preset **{name}** แล้ว",
    },
    "preset.saved": {
        "en": "💾 Preset **{name}** saved.",
        "th": "💾 บันทึก Preset **{name}** แล้ว",
    },
    "preset.not_found": {
        "en": "Preset `{name}` not found.",
        "th": "ไม่พบ Preset `{name}`",
    },
    "preset.deleted": {
        "en": "Preset `{name}` deleted.",
        "th": "ลบ Preset `{name}` แล้ว",
    },

    # ── Health Report (F35) ───────────────────────────────────────────────────
    "health.title": {
        "en": "🩺 Health Report",
        "th": "🩺 รายงานสถานะ",
    },
    "health.ok": {
        "en": "✅ OK",
        "th": "✅ ปกติ",
    },
    "health.warning": {
        "en": "⚠️ Warning",
        "th": "⚠️ คำเตือน",
    },
    "health.error": {
        "en": "❌ Error",
        "th": "❌ ผิดพลาด",
    },

    # ── Language toggle ───────────────────────────────────────────────────────
    "lang.set": {
        "en": "🌐 Language set to **English**.",
        "th": "🌐 เปลี่ยนภาษาเป็น **ภาษาไทย** แล้ว",
    },
}

# ── Translation engine ────────────────────────────────────────────────────────

_SUPPORTED_LOCALES = frozenset({"en", "th"})
_DEFAULT_LOCALE    = "en"


def t(key: str, locale: str = "en", **kwargs) -> str:
    """
    Translate a string key into the given locale.

    Falls back to English if the key is not available in the target locale.
    Falls back to the key itself if not available in English either.
    Applies str.format(**kwargs) if keyword args are given.

    Example:
        t("queue.cleared", "th", count=5)
        → "ล้างคิวแล้ว — ลบ 5 เพลง"
    """
    if locale not in _SUPPORTED_LOCALES:
        locale = _DEFAULT_LOCALE

    entry = _STRINGS.get(key)
    if entry is None:
        logger.debug("i18n: missing key %r", key)
        raw = key
    else:
        raw = entry.get(locale) or entry.get(_DEFAULT_LOCALE) or key

    if kwargs:
        try:
            return raw.format(**kwargs)
        except (KeyError, ValueError):
            logger.debug("i18n: format error for key %r with %r", key, kwargs)
            return raw
    return raw


# ── Locale cache (guild_id → locale string) ───────────────────────────────────

_locale_cache: dict[int, str] = {}


async def get_locale(guild_id: int, db=None) -> str:
    """
    Return the locale for a guild ("en" or "th").
    Uses an in-memory cache so DB is only hit once per session.
    """
    if guild_id in _locale_cache:
        return _locale_cache[guild_id]

    locale = _DEFAULT_LOCALE
    if db:
        try:
            cfg    = await db.get_server_config(guild_id)
            locale = getattr(cfg, "language", _DEFAULT_LOCALE)
            if locale not in _SUPPORTED_LOCALES:
                locale = _DEFAULT_LOCALE
        except Exception:
            pass

    _locale_cache[guild_id] = locale
    return locale


def set_locale_cache(guild_id: int, locale: str) -> None:
    """Update the in-memory locale cache after a /language command."""
    if locale in _SUPPORTED_LOCALES:
        _locale_cache[guild_id] = locale


def supported_locales() -> list[str]:
    return sorted(_SUPPORTED_LOCALES)
