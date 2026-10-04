# -*- coding: utf-8 -*-
"""
pan/filter.py — FFmpeg Audio Pan (L-R balance) and Stereo Soundstage Enhancer (Feature 2.8).
"""

from __future__ import annotations

from typing import Optional


def clamp_balance(val: float) -> float:
    """Clamp balance value between -1.0 (Full Left) and 1.0 (Full Right)."""
    return max(-1.0, min(1.0, float(val)))


def clamp_width(val: float) -> float:
    """Clamp stereo width multiplier between 0.0 (Mono) and 3.0 (Extreme Wide)."""
    return max(0.0, min(3.0, float(val)))


def build_pan_filter(balance: float) -> Optional[str]:
    """
    Generate FFmpeg pan filter for Left-Right stereo balance.

    balance:
      -1.0 = 100% Left (Right channel muted)
       0.0 = Center / Normal (bypassed, returns None)
      +1.0 = 100% Right (Left channel muted)
    """
    b = clamp_balance(balance)
    if abs(b) < 0.02:
        return None

    if b < 0:
        # Panned to Left: keep Left full, attenuate Right
        left_gain = 1.0
        right_gain = max(0.0, 1.0 + b)
    else:
        # Panned to Right: attenuate Left, keep Right full
        left_gain = max(0.0, 1.0 - b)
        right_gain = 1.0

    return f"pan=stereo|c0={left_gain:.2f}*c0|c1={right_gain:.2f}*c1"


def build_stereo_enhance_filter(width: float) -> Optional[str]:
    """
    Generate FFmpeg extrastereo filter for widening or narrowing soundstage.

    width:
       0.0 = Mono
       0.5 = Narrow
       1.0 = Default / Normal (bypassed, returns None)
       1.5 = Wide
       2.0 = Ultra-Wide
       2.5 = Expanded Stage
    """
    w = clamp_width(width)
    if abs(w - 1.0) < 0.05:
        return None

    return f"extrastereo=m={w:.2f}"
