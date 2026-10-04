# -*- coding: utf-8 -*-
"""
tests/test_i18n.py — Unit tests for core/i18n.py (multi-language localization).

Run with:
    python -m pytest tests/test_i18n.py -v

Covers:
  1. JSON loading — all 9 locale files are valid and loaded correctly
  2. Key completeness — every locale has all keys present in English
  3. Format parameters — {placeholders} work correctly in all locales
  4. Fallback chain — missing key falls back to English; unknown locale falls back to English
  5. Alias resolution — "zh-CN", "zh", "en-US", "de-DE" etc. resolve correctly
  6. Supported locales — correct list returned
  7. Locale cache — set/get/invalidate work correctly
  8. get_locale_meta — returns correct metadata for all locales
"""

from __future__ import annotations

import sys
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the project root is on sys.path so "core.i18n" can be imported.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import core.i18n as i18n_module
from core.i18n import (
    t,
    get_locale,
    set_locale_cache,
    invalidate_locale_cache,
    supported_locales,
    get_locale_meta,
    _normalise,
    _STRINGS,
    _LOCALES_DIR,
    _DEFAULT_LOCALE,
    LOCALE_META,
)

# ── Constants ─────────────────────────────────────────────────────────────────

EXPECTED_LOCALES = {"en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"}


# ── 1. JSON loading ───────────────────────────────────────────────────────────

class TestJsonLoading:
    def test_locales_dir_exists(self):
        assert _LOCALES_DIR.is_dir(), (
            f"locales/ directory not found at: {_LOCALES_DIR}"
        )

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_locale_dir_exists(self, locale: str):
        locale_dir = _LOCALES_DIR / locale
        assert locale_dir.is_dir(), f"Missing locale directory: locales/{locale}/"

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_strings_json_exists(self, locale: str):
        strings_file = _LOCALES_DIR / locale / "strings.json"
        assert strings_file.is_file(), f"Missing file: locales/{locale}/strings.json"

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_strings_json_is_valid_json(self, locale: str):
        strings_file = _LOCALES_DIR / locale / "strings.json"
        with strings_file.open(encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict), (
            f"locales/{locale}/strings.json must be a JSON object"
        )
        assert len(data) > 0, f"locales/{locale}/strings.json is empty"

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_locale_loaded_in_strings(self, locale: str):
        assert locale in _STRINGS, (
            f"Locale {locale!r} was not loaded into _STRINGS"
        )

    def test_all_expected_locales_loaded(self):
        loaded = set(supported_locales())
        assert EXPECTED_LOCALES <= loaded, (
            f"Missing locales: {EXPECTED_LOCALES - loaded}"
        )


# ── 2. Key completeness ───────────────────────────────────────────────────────

class TestKeyCompleteness:
    """Every locale must contain all keys present in English (en)."""

    @pytest.fixture(autouse=True)
    def en_keys(self):
        self.en_keys: set[str] = set(_STRINGS.get("en", {}).keys())
        assert self.en_keys, "English locale has no keys — check locales/en/strings.json"

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES - {"en"}))
    def test_no_missing_keys(self, locale: str):
        locale_keys = set(_STRINGS.get(locale, {}).keys())
        missing = self.en_keys - locale_keys
        assert not missing, (
            f"Locale {locale!r} is missing {len(missing)} key(s): {sorted(missing)}"
        )

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_all_values_are_strings(self, locale: str):
        for key, val in _STRINGS.get(locale, {}).items():
            assert isinstance(val, str), (
                f"locale={locale!r}, key={key!r}: value must be a string, got {type(val)}"
            )

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_no_empty_values(self, locale: str):
        for key, val in _STRINGS.get(locale, {}).items():
            assert val.strip(), (
                f"locale={locale!r}, key={key!r}: value is empty or whitespace-only"
            )


# ── 3. Format parameters ──────────────────────────────────────────────────────

class TestFormatParameters:
    """Verify that all format placeholders can be filled without errors."""

    FORMAT_CASES = [
        # (key, kwargs)
        ("queue.added",    {"title": "Test Song", "pos": 1}),
        ("queue.cleared",  {"count": 5}),
        ("queue.shuffled", {"count": 10}),
        ("queue.removed",  {"title": "Test Song", "pos": 2}),
        ("queue.moved",    {"from_pos": 1, "to_pos": 3}),
        ("queue.invalid_pos", {"pos": 99}),
        ("skip.vote",      {"votes": 2, "needed": 3}),
        ("volume.set",     {"vol": 75}),
        ("error.no_results", {"query": "never gonna give you up"}),
        ("error.too_long", {"max": 60}),
        ("sleep.set",      {"duration": "30min"}),
        ("undo.success",   {"op": "clear", "count": 5}),
        ("effects.applied", {"effect": "bass_boost"}),
        ("speed.set",      {"rate": 1.5}),
        ("pitch.set",      {"semitones": 2}),
        ("crossfade.set",  {"secs": 3}),
        ("preset.applied", {"name": "chill"}),
        ("preset.saved",   {"name": "chill"}),
        ("preset.not_found", {"name": "nonexistent"}),
        ("preset.deleted", {"name": "chill"}),
        ("queue.shuffled_added", {"count": 10}),
        ("autoplay.up_next", {"title": "Test Song"}),
        ("seek.success",     {"time": "1:30"}),
        ("seek.forward",     {"seconds": 15, "time": "2:00"}),
        ("seek.rewind",      {"seconds": 15, "time": "1:00"}),
        ("seek.replay",      {"title": "Test Song"}),
        ("seek.out_of_range", {"duration": "3:45"}),
        ("equalizer.preset_applied", {"name": "Pop"}),
        ("equalizer.custom_applied", {"sub_bass": "+2.0", "bass": "+1.0", "mid": "+2.0", "treble": "+3.0"}),
        ("equalizer.invalid_preset", {"name": "invalid", "presets": "pop, rock"}),
        ("loopab.started", {"start": "0:30", "end": "1:15"}),
        ("loopab.invalid_times", {"start": "1:15", "end": "0:30"}),
        ("pan.set", {"position": "Center (0%)"}),
        ("stereo.set", {"mode": "Wide (1.5x)"}),
    ]

    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    @pytest.mark.parametrize("key,kwargs", FORMAT_CASES)
    def test_format_does_not_raise(self, locale: str, key: str, kwargs: dict):
        result = t(key, locale, **kwargs)
        assert isinstance(result, str), (
            f"t({key!r}, {locale!r}, ...) did not return a string"
        )
        # Ensure format placeholders are not visible in the output
        for placeholder in kwargs:
            assert "{" + placeholder not in result, (
                f"Placeholder {{{placeholder}}} was not replaced in locale={locale!r}, key={key!r}: {result!r}"
            )


# ── 4. Fallback chain ─────────────────────────────────────────────────────────

class TestFallbackChain:
    def test_unknown_key_falls_back_to_key_itself(self):
        result = t("this.key.does.not.exist", "en")
        assert result == "this.key.does.not.exist"

    def test_missing_key_in_locale_falls_back_to_english(self, tmp_path, monkeypatch):
        """Simulate a locale with a missing key and verify fallback to en."""
        # Patch _STRINGS to include a partial locale
        fake_strings = {
            "en": {"test.key": "English value", "other.key": "Other EN"},
            "xx": {"other.key": "Other XX"},  # "test.key" is missing
        }
        monkeypatch.setattr(i18n_module, "_STRINGS", fake_strings)
        monkeypatch.setattr(
            i18n_module, "_SUPPORTED_LOCALES", frozenset(fake_strings.keys())
        )
        result = t("test.key", "xx")
        assert result == "English value", (
            f"Expected fallback to English 'English value', got {result!r}"
        )

    def test_unknown_locale_falls_back_to_english(self):
        result = t("health.ok", "xx_unknown")
        # Should return the English value
        assert result == _STRINGS["en"]["health.ok"]

    def test_good_key_in_good_locale_returns_correct_string(self):
        result = t("health.ok", "en")
        assert result == "✅ OK"

    def test_thai_translation_returned_correctly(self):
        result = t("health.ok", "th")
        assert "ปกติ" in result

    def test_japanese_translation_returned_correctly(self):
        result = t("now_playing.title", "ja")
        assert "再生中" in result

    def test_korean_translation_returned_correctly(self):
        result = t("skip.done", "ko")
        assert "건너뛰었습니다" in result


# ── 5. Alias resolution ───────────────────────────────────────────────────────

class TestAliasResolution:
    ALIAS_CASES = [
        ("zh",       "zh_cn"),
        ("zh-cn",    "zh_cn"),
        ("zh_hans",  "zh_cn"),
        ("ja-jp",    "ja"),
        ("ko-kr",    "ko"),
        ("es-es",    "es"),
        ("es-MX",    "es"),
        ("ru-ru",    "ru"),
        ("fr-fr",    "fr"),
        ("fr-BE",    "fr"),
        ("de-de",    "de"),
        ("de-AT",    "de"),
        ("th-th",    "th"),
        ("en-us",    "en"),
        ("en-GB",    "en"),
        ("EN",       "en"),
        ("TH",       "th"),
    ]

    @pytest.mark.parametrize("alias,expected", ALIAS_CASES)
    def test_alias_resolves_correctly(self, alias: str, expected: str):
        assert _normalise(alias) == expected, (
            f"Expected _normalise({alias!r}) == {expected!r}"
        )

    def test_unknown_alias_resolves_to_default(self):
        assert _normalise("xx_UNKNOWN") == _DEFAULT_LOCALE

    def test_t_accepts_alias_and_resolves(self):
        result_alias = t("health.ok", "zh-CN")
        result_canonical = t("health.ok", "zh_cn")
        assert result_alias == result_canonical


# ── 6. Supported locales ──────────────────────────────────────────────────────

class TestSupportedLocales:
    def test_supported_locales_returns_list(self):
        result = supported_locales()
        assert isinstance(result, list)

    def test_supported_locales_is_sorted(self):
        result = supported_locales()
        assert result == sorted(result)

    def test_all_expected_locales_in_supported(self):
        result = set(supported_locales())
        assert EXPECTED_LOCALES <= result


# ── 7. Locale cache ───────────────────────────────────────────────────────────

class TestLocaleCache:
    def setup_method(self):
        # Clear the cache before each test
        i18n_module._locale_cache.clear()

    def test_set_locale_cache(self):
        set_locale_cache(123456, "th")
        assert i18n_module._locale_cache[123456] == "th"

    def test_set_locale_cache_normalises_alias(self):
        set_locale_cache(999, "zh-CN")
        assert i18n_module._locale_cache[999] == "zh_cn"

    def test_set_locale_cache_ignores_unsupported(self):
        set_locale_cache(111, "xx_unknown")
        assert 111 not in i18n_module._locale_cache

    def test_invalidate_locale_cache(self):
        i18n_module._locale_cache[777] = "ja"
        invalidate_locale_cache(777)
        assert 777 not in i18n_module._locale_cache

    def test_invalidate_nonexistent_is_noop(self):
        invalidate_locale_cache(999999)  # should not raise

    def test_get_locale_returns_cached(self):
        i18n_module._locale_cache[555] = "ko"
        result = asyncio.run(get_locale(555, db=None))
        assert result == "ko"

    def test_get_locale_returns_default_without_db(self):
        result = asyncio.run(get_locale(888888, db=None))
        assert result == _DEFAULT_LOCALE

    def test_get_locale_reads_from_db(self):
        """get_locale() should load from DB when not cached."""
        mock_cfg = MagicMock()
        mock_cfg.language = "fr"
        mock_db = AsyncMock()
        mock_db.get_server_config.return_value = mock_cfg

        result = asyncio.run(get_locale(424242, db=mock_db))
        assert result == "fr"
        # Should be cached now
        assert i18n_module._locale_cache[424242] == "fr"


# ── 8. get_locale_meta ────────────────────────────────────────────────────────

class TestGetLocaleMeta:
    @pytest.mark.parametrize("locale", sorted(EXPECTED_LOCALES))
    def test_meta_has_required_fields(self, locale: str):
        meta = get_locale_meta(locale)
        for field in ("name", "native_name", "flag", "color", "choice_label"):
            assert field in meta, (
                f"get_locale_meta({locale!r}) missing field: {field!r}"
            )

    def test_meta_unknown_locale_returns_english(self):
        meta = get_locale_meta("xx_unknown")
        assert meta == LOCALE_META["en"]

    @pytest.mark.parametrize("locale,expected_flag", [
        ("en",    "🇬🇧"),
        ("th",    "🇹🇭"),
        ("zh_cn", "🇨🇳"),
        ("ja",    "🇯🇵"),
        ("ko",    "🇰🇷"),
        ("es",    "🇪🇸"),
        ("ru",    "🇷🇺"),
        ("fr",    "🇫🇷"),
        ("de",    "🇩🇪"),
    ])
    def test_locale_flag(self, locale: str, expected_flag: str):
        meta = get_locale_meta(locale)
        assert meta["flag"] == expected_flag, (
            f"Expected flag {expected_flag!r} for locale {locale!r}, got {meta['flag']!r}"
        )
