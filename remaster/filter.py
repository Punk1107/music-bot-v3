# -*- coding: utf-8 -*-
"""remaster/filter.py — FFmpeg Audio Clarity & Sub-Harmonic Remaster filter builder."""

from __future__ import annotations

from typing import Optional

# Clarity Level 1 to 5 mapping (capped <= 3.0 to prevent high-frequency noise distortion)
CLARITY_LEVEL_MAP: dict[int, float] = {
    1: 1.4,
    2: 1.8,
    3: 2.2,
    4: 2.6,
    5: 3.0,
}

SUB_HARMONIC_FILTER: str = (
    "equalizer=f=45:t=q:w=1.2:g=4.5,"
    "compand=attacks=0.05:decays=0.2:points=-60/-60|-24/-20|0/-3"
)


def build_clarity_filter(level: int = 2) -> str:
    """Return crystalizer filter with intensity safely clamped <= 3.0."""
    clamped_level = max(1, min(5, int(level)))
    intensity = CLARITY_LEVEL_MAP.get(clamped_level, 1.8)
    return f"crystalizer=i={intensity:.1f}"


def build_remaster_filter(enabled: bool, clarity_level: int = 2) -> Optional[str]:
    """
    Build combined FFmpeg filter for audio remastering:
    - High-frequency restoration (crystalizer)
    - Deep punch sub-bass (equalizer + dynamic compand)
    """
    if not enabled:
        return None
    clarity = build_clarity_filter(clarity_level)
    return f"{clarity},{SUB_HARMONIC_FILTER}"

