# -*- coding: utf-8 -*-
"""
pan/ — Left-Right Audio Balance & Stereo Widening (Feature 2.8).
"""

from __future__ import annotations

from pan.cog import PanCog
from pan.filter import (
    build_pan_filter,
    build_stereo_enhance_filter,
    clamp_balance,
    clamp_width,
)

__all__ = [
    "PanCog",
    "build_pan_filter",
    "build_stereo_enhance_filter",
    "clamp_balance",
    "clamp_width",
]
