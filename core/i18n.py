# -*- coding: utf-8 -*-
"""
core/i18n.py — Localization (i18n) for Music Bot V3.

Tier Performance F30: Multi-language localization (9 languages).

Supported locales:
  "en"    — English (default / fallback)
  "th"    — Thai
  "zh_cn" — Chinese (Simplified)
  "ja"    — Japanese
  "ko"    — Korean
  "es"    — Spanish
  "ru"    — Russian
  "fr"    — French
  "de"    — German

String catalogues are loaded from individual JSON files under:
  locales/<locale>/strings.json

Locale aliases (e.g. "zh-CN" → "zh_cn") are resolved automatically.

Usage:
    from core.i18n import t, get_locale, set_locale_cache

    locale = await get_locale(guild_id, bot.db)
    msg    = t("now_playing.title", locale)

    # With format params:
    msg = t("queue.added", locale, title="Song", pos=3)

Fallback chain:
  1. Target locale
  2. English ("en")
  3. The key itself
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager

logger = logging.getLogger(__name__)

# ── Locale aliases ─────────────────────────────────────────────────────────────
# Maps alternative tags → canonical locale code used for folder names.

_LOCALE_ALIASES: dict[str, str] = {
    # Chinese Simplified variants
    "zh":    "zh_cn",
    "zh-cn": "zh_cn",
    "zh_hans": "zh_cn",
    # Japanese
    "ja-jp": "ja",
    # Korean
    "ko-kr": "ko",
    # Spanish
    "es-es": "es",
    "es-mx": "es",
    "es-419": "es",
    # Russian
    "ru-ru": "ru",
    # French
    "fr-fr": "fr",
    "fr-be": "fr",
    "fr-ca": "fr",
    # German
    "de-de": "de",
    "de-at": "de",
    "de-ch": "de",
    # Thai
    "th-th": "th",
    # English
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
}

# ── Locale metadata ────────────────────────────────────────────────────────────

LOCALE_META: dict[str, dict] = {
    "en": {
        "name":        "English",
        "native_name": "English",
        "flag":        "🇬🇧",
        "color":       0x5865F2,
        "choice_label": "🇬🇧 English",
    },
    "th": {
        "name":        "Thai",
        "native_name": "ภาษาไทย",
        "flag":        "🇹🇭",
        "color":       0xA8385D,
        "choice_label": "🇹🇭 ภาษาไทย (Thai)",
    },
    "zh_cn": {
        "name":        "Chinese (Simplified)",
        "native_name": "简体中文",
        "flag":        "🇨🇳",
        "color":       0xDE2910,
        "choice_label": "🇨🇳 简体中文 (Chinese Simplified)",
    },
    "ja": {
        "name":        "Japanese",
        "native_name": "日本語",
        "flag":        "🇯🇵",
        "color":       0xBC002D,
        "choice_label": "🇯🇵 日本語 (Japanese)",
    },
    "ko": {
        "name":        "Korean",
        "native_name": "한국어",
        "flag":        "🇰🇷",
        "color":       0x0047A0,
        "choice_label": "🇰🇷 한국어 (Korean)",
    },
    "es": {
        "name":        "Spanish",
        "native_name": "Español",
        "flag":        "🇪🇸",
        "color":       0xC60B1E,
        "choice_label": "🇪🇸 Español (Spanish)",
    },
    "ru": {
        "name":        "Russian",
        "native_name": "Русский",
        "flag":        "🇷🇺",
        "color":       0x0039A6,
        "choice_label": "🇷🇺 Русский (Russian)",
    },
    "fr": {
        "name":        "French",
        "native_name": "Français",
        "flag":        "🇫🇷",
        "color":       0x0055A4,
        "choice_label": "🇫🇷 Français (French)",
    },
    "de": {
        "name":        "German",
        "native_name": "Deutsch",
        "flag":        "🇩🇪",
        "color":       0x000000,
        "choice_label": "🇩🇪 Deutsch (German)",
    },
}

# ── JSON loader ────────────────────────────────────────────────────────────────

# _STRINGS maps: locale_code -> { key -> translated_string }
_STRINGS: dict[str, dict[str, str]] = {}

_DEFAULT_LOCALE = "en"

# Locate the locales/ directory relative to this file's location.
# Layout:  <project_root>/locales/<lang>/strings.json
#          <project_root>/core/i18n.py
_LOCALES_DIR: Path = Path(__file__).parent.parent / "locales"


def _load_locales() -> None:
    """
    Scan the locales/ directory and load every strings.json file found.
    Called once at module import time.
    Missing locale directories are silently skipped (warning is logged).
    """
    if not _LOCALES_DIR.is_dir():
        logger.warning(
            "i18n: locales directory not found at %s — no translations loaded",
            _LOCALES_DIR,
        )
        return

    loaded: list[str] = []
    for lang_dir in sorted(_LOCALES_DIR.iterdir()):
        if not lang_dir.is_dir():
            continue
        locale_code = lang_dir.name  # e.g. "en", "th", "zh_cn"
        strings_file = lang_dir / "strings.json"
        if not strings_file.is_file():
            logger.warning(
                "i18n: locale dir %r has no strings.json — skipped", locale_code
            )
            continue
        try:
            with strings_file.open(encoding="utf-8") as f:
                data: dict[str, str] = json.load(f)
            _STRINGS[locale_code] = data
            loaded.append(locale_code)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(
                "i18n: failed to load %s: %s", strings_file, exc
            )

    logger.info("i18n: loaded locales: %s", loaded)


# Load immediately on import.
_load_locales()

# Derived set of supported locales (based on what was actually loaded).
_SUPPORTED_LOCALES: frozenset[str] = frozenset(_STRINGS.keys())


# ── Locale normaliser ──────────────────────────────────────────────────────────

def _normalise(locale: str) -> str:
    """
    Normalise and resolve alias for a locale tag.
    Returns a canonical locale code (e.g. "zh_cn", "en") or the default
    if the input is unsupported.

    Resolution order:
      1. Lowercase with dashes preserved → check alias table (aliases use dashes)
      2. Lowercase with dashes → underscores → check supported locales directly
      3. Fall back to _DEFAULT_LOCALE
    """
    lowered_with_dashes = locale.lower()           # e.g. "ja-jp", "en-us"
    lowered_underscored = lowered_with_dashes.replace("-", "_")  # e.g. "ja_jp", "en_us"

    # 1. Alias table lookup (keys use dashes, e.g. "ja-jp")
    canonical = _LOCALE_ALIASES.get(lowered_with_dashes)
    if canonical and canonical in _SUPPORTED_LOCALES:
        return canonical

    # 2. Direct match with underscore form (canonical locale codes)
    if lowered_underscored in _SUPPORTED_LOCALES:
        return lowered_underscored

    # 3. Direct match with dash form (unlikely but safe)
    if lowered_with_dashes in _SUPPORTED_LOCALES:
        return lowered_with_dashes

    logger.debug("i18n: unknown locale %r, falling back to %r", locale, _DEFAULT_LOCALE)
    return _DEFAULT_LOCALE


# ── Translation function ───────────────────────────────────────────────────────

def t(key: str, locale: str = "en", **kwargs) -> str:
    """
    Translate a string key into the given locale.

    Fallback chain:
      1. Target locale's strings.json
      2. English ("en") strings.json
      3. The key itself

    Applies str.format(**kwargs) if keyword args are given.

    Example:
        t("queue.cleared", "th", count=5)
        → "ล้างคิวแล้ว — ลบ 5 เพลง"
    """
    canonical = _normalise(locale)

    # 1. Target locale
    raw: str | None = _STRINGS.get(canonical, {}).get(key)

    # 2. English fallback
    if raw is None:
        raw = _STRINGS.get(_DEFAULT_LOCALE, {}).get(key)
        if raw is not None and canonical != _DEFAULT_LOCALE:
            logger.debug(
                "i18n: key %r missing in %r, fell back to %r",
                key, canonical, _DEFAULT_LOCALE,
            )

    # 3. Key itself as last resort
    if raw is None:
        logger.debug("i18n: key %r not found in any locale", key)
        raw = key

    if kwargs:
        try:
            return raw.format(**kwargs)
        except (KeyError, ValueError):
            logger.debug(
                "i18n: format error for key %r with %r", key, kwargs
            )
            return raw

    return raw


# ── Locale cache (guild_id → locale string) ───────────────────────────────────

_locale_cache: dict[int, str] = {}


async def get_locale(guild_id: int, db=None) -> str:
    """
    Return the canonical locale code for a guild.

    Lookup order:
      1. In-memory cache (O(1) per call after first access)
      2. DatabaseManager.get_server_config()
      3. Default ("en")
    """
    if guild_id in _locale_cache:
        return _locale_cache[guild_id]

    locale = _DEFAULT_LOCALE
    if db:
        try:
            cfg    = await db.get_server_config(guild_id)
            raw    = getattr(cfg, "language", _DEFAULT_LOCALE)
            locale = _normalise(raw)
        except Exception:
            pass

    _locale_cache[guild_id] = locale
    return locale


def set_locale_cache(guild_id: int, locale: str) -> None:
    """Update the in-memory locale cache after a /language command."""
    lowered = locale.lower()
    # Resolve alias, but only accept the result if it is truly a supported locale
    # (do NOT fall back to default — callers should only pass valid locales).
    canonical = _LOCALE_ALIASES.get(lowered, lowered.replace("-", "_"))
    if canonical in _SUPPORTED_LOCALES:
        _locale_cache[guild_id] = canonical
    else:
        logger.warning(
            "i18n: set_locale_cache called with unsupported locale %r — ignored", locale
        )


def invalidate_locale_cache(guild_id: int) -> None:
    """Remove a guild's locale from the in-memory cache (forces DB re-read)."""
    _locale_cache.pop(guild_id, None)


def supported_locales() -> list[str]:
    """Return a sorted list of all loaded locale codes."""
    return sorted(_SUPPORTED_LOCALES)


def get_locale_meta(locale: str) -> dict:
    """
    Return metadata dict for a locale (name, native_name, flag, color, choice_label).
    Falls back to English metadata if the locale is unknown.
    """
    canonical = _normalise(locale)
    return LOCALE_META.get(canonical, LOCALE_META[_DEFAULT_LOCALE])


def reload_locales() -> None:
    """
    Hot-reload all locale files from disk.
    Useful for development / live translation updates without restarting the bot.
    Also clears the guild locale cache so next t() calls re-resolve.
    """
    global _SUPPORTED_LOCALES
    _STRINGS.clear()
    _locale_cache.clear()
    _load_locales()
    _SUPPORTED_LOCALES = frozenset(_STRINGS.keys())
    logger.info("i18n: hot-reloaded all locales — cache cleared")
