# -*- coding: utf-8 -*-
"""spatial/filter.py — FFmpeg Spatial 3D & Binaural Headphone Virtualizer."""

from __future__ import annotations

from typing import Optional

SPATIAL_PRESETS: dict[str, str] = {
    "binaural": "earwax",
    "hall":     "aecho=0.8:0.88:60|120:0.4|0.28,surround=chl_out=stereo",
    "studio":   "aecho=0.8:0.7:20:0.25",
    "club":     "aecho=0.8:0.88:40|80:0.45|0.35,bass=g=4",
}


def build_spatial_filter(preset: Optional[str]) -> Optional[str]:
    """
    Build FFmpeg audio filter string for Spatial 3D & Binaural virtualization.

    Presets:
      - binaural: FFmpeg earwax HRTF simulation for headphones
      - hall:     Concert Hall acoustic simulation (aecho + stereo surround)
      - studio:   Dry intimate recording room with damped 20ms reflections
      - club:     Club arena acoustics with bass boost (+4dB)
      - off/None: Disable spatial processing
    """
    if not preset or preset.lower() in ("off", "none", "disable", "disabled"):
        return None
    return SPATIAL_PRESETS.get(preset.lower().strip())

