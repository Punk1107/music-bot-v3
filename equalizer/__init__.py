# -*- coding: utf-8 -*-
"""
equalizer/ — 4-Band Frequency Equalizer & Presets (Feature 2.5).
"""

from __future__ import annotations

from equalizer.cog import EqualizerCog
from equalizer.presets import (
    EQ_BANDS,
    EQ_PRESETS,
    PRESET_NAMES,
    build_equalizer_filter,
    clamp_gain,
)

__all__ = [
    "EqualizerCog",
    "EQ_BANDS",
    "EQ_PRESETS",
    "PRESET_NAMES",
    "build_equalizer_filter",
    "clamp_gain",
]
