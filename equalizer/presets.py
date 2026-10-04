# -*- coding: utf-8 -*-
"""
equalizer/presets.py — Equalizer bands, presets, and FFmpeg filter generator (Feature 2.5).
"""

from __future__ import annotations

from typing import Optional

# ── Frequency Band Definitions ───────────────────────────────────────────────
# Standard 4-band audio equalizer frequencies
EQ_BANDS = {
    "sub_bass": {"freq": 60,   "label": "Sub-bass (60Hz)", "q": 1.0},
    "bass":     {"freq": 250,  "label": "Bass (250Hz)",     "q": 1.0},
    "mid":      {"freq": 1000, "label": "Mid (1kHz)",       "q": 1.0},
    "treble":   {"freq": 8000, "label": "Treble (8kHz)",    "q": 1.0},
}

# ── Equalizer Presets ────────────────────────────────────────────────────────
EQ_PRESETS: dict[str, dict[str, float]] = {
    "pop": {
        "sub_bass": 2.0,
        "bass":     1.0,
        "mid":      2.0,
        "treble":   3.0,
    },
    "rock": {
        "sub_bass": 4.0,
        "bass":     3.0,
        "mid":     -1.0,
        "treble":   3.0,
    },
    "electronic": {
        "sub_bass": 5.0,
        "bass":     3.0,
        "mid":      0.0,
        "treble":   4.0,
    },
    "vocal": {
        "sub_bass": -2.0,
        "bass":     -1.0,
        "mid":       4.0,
        "treble":    2.0,
    },
    "flat": {
        "sub_bass": 0.0,
        "bass":     0.0,
        "mid":      0.0,
        "treble":   0.0,
    },
}

PRESET_NAMES = list(EQ_PRESETS.keys())


def clamp_gain(gain: float, min_gain: float = -10.0, max_gain: float = 10.0) -> float:
    """Clamp gain value between min_gain and max_gain dB."""
    return max(min_gain, min(max_gain, float(gain)))


def build_equalizer_filter(bands: Optional[dict[str, float]]) -> Optional[str]:
    """
    Generate FFmpeg filter chain for 4-band equalizer.
    Returns None if all gains are 0.0 or bands dictionary is empty.
    """
    if not bands:
        return None

    filters: list[str] = []
    has_active_gain = False

    for band_key, meta in EQ_BANDS.items():
        raw_gain = bands.get(band_key, 0.0)
        gain = clamp_gain(raw_gain)
        if abs(gain) > 0.05:
            has_active_gain = True
        freq = meta["freq"]
        q = meta["q"]
        filters.append(f"equalizer=f={freq}:t=q:w={q}:g={gain:.1f}")

    if not has_active_gain:
        return None

    return ",".join(filters)
