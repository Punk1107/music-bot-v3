# -*- coding: utf-8 -*-
"""
loudnorm/filter.py — FFmpeg EBU R128 Loudness Normalization filter builder (Feature 2.6 & Gen 5.0).
"""

from __future__ import annotations

from typing import Optional

# EBU R128 / ITU-R BS.1770 broadcast & streaming loudness targets
DEFAULT_TARGET_I   = -16.0  # Integrated loudness target (LUFS)
DEFAULT_TRUE_PEAK  = -1.5   # Maximum true peak (dBTP) to prevent clipping
DEFAULT_LRA        = 11.0   # Loudness range target (LU)

# Gen 5.0 Multi-Profile Loudness Targets
LOUDNORM_PROFILES: dict[str, dict[str, float]] = {
    "broadcast":  {"i": -16.0, "tp": -1.5, "lra": 11.0},
    "podcast":    {"i": -14.0, "tp": -1.0, "lra": 7.0},
    "background": {"i": -20.0, "tp": -2.0, "lra": 14.0},
    "party":      {"i": -12.0, "tp": -0.5, "lra": 8.0},
}


def build_loudnorm_filter(
    integrated_loudness: Optional[float] = None,
    true_peak:           Optional[float] = None,
    loudness_range:      Optional[float] = None,
    profile:             str             = "broadcast",
) -> str:
    """
    Build FFmpeg loudnorm audio filter string.

    Supports custom values or pre-configured content profiles:
      - broadcast:  I=-16.0, TP=-1.5, LRA=11.0 (Standard EBU R128)
      - podcast:    I=-14.0, TP=-1.0, LRA=7.0  (Clear dialogue & spoken content)
      - background: I=-20.0, TP=-2.0, LRA=14.0 (Soft acoustic / study background)
      - party:      I=-12.0, TP=-0.5, LRA=8.0  (High energy / club punch)
    """
    prof = LOUDNORM_PROFILES.get(profile.lower(), LOUDNORM_PROFILES["broadcast"])

    i_val = prof["i"] if integrated_loudness is None else integrated_loudness
    tp_val = prof["tp"] if true_peak is None else true_peak
    lra_val = prof["lra"] if loudness_range is None else loudness_range

    return f"loudnorm=I={i_val:.1f}:TP={tp_val:.1f}:LRA={lra_val:.1f}"
