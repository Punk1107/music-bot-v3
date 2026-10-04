# -*- coding: utf-8 -*-
"""
loudnorm/filter.py — FFmpeg EBU R128 Loudness Normalization filter builder (Feature 2.6).
"""

from __future__ import annotations

# EBU R128 / ITU-R BS.1770 broadcast & streaming loudness targets
DEFAULT_TARGET_I   = -16.0  # Integrated loudness target (LUFS)
DEFAULT_TRUE_PEAK  = -1.5   # Maximum true peak (dBTP) to prevent clipping
DEFAULT_LRA        = 11.0   # Loudness range target (LU)


def build_loudnorm_filter(
    integrated_loudness: float = DEFAULT_TARGET_I,
    true_peak:           float = DEFAULT_TRUE_PEAK,
    loudness_range:      float = DEFAULT_LRA,
) -> str:
    """
    Build FFmpeg loudnorm audio filter string.

    loudnorm performs single-pass linear normalization adhering to EBU R128,
    providing uniform perceptual volume across varied music tracks without the
    unpleasant pumping artifacts of dynamic compression/dynaudnorm.
    """
    return f"loudnorm=I={integrated_loudness:.1f}:TP={true_peak:.1f}:LRA={loudness_range:.1f}"
