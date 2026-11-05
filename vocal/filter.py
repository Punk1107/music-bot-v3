# -*- coding: utf-8 -*-
"""vocal/filter.py — FFmpeg Mid-Side Vocal Remover & Acapella Isolator filter builder."""

from __future__ import annotations

from typing import Optional

# FFmpeg stereotools mlev and slev minimum allowed value is 1/64 = 0.015625 (~ -36.1 dB attenuation)
MIN_STEREO_LEVEL = 0.015625
MAX_STEREO_LEVEL = 64.0


def build_vocal_filter(mode: Optional[str], suppression: int = 100) -> Optional[str]:
    """
    Build FFmpeg audio filter string for vocal cancellation or acapella isolation.

    - "remove": Mid-Side cancellation of center-panned vocals while preserving stereo side instruments.
      mlev is dynamically attenuated based on suppression (1-100%).
    - "isolate": Extracts mid-channel vocal signal combined with human vocal frequency bandpass.
    - "off" or None: Returns None.
    """
    if not mode or mode.lower() in ("off", "none", "disable", "disabled"):
        return None

    norm_mode = mode.lower().strip()
    suppression = max(1, min(100, int(suppression)))

    if norm_mode == "remove":
        # Calculate mid level: at 100% suppression, mlev is MIN_STEREO_LEVEL (0.015625).
        # At 1% suppression, mlev is ~0.99.
        ratio = suppression / 100.0
        mlev = max(MIN_STEREO_LEVEL, min(1.0, 1.0 - ratio * (1.0 - MIN_STEREO_LEVEL)))
        return f"stereotools=mlev={mlev:.6f}:slev=1.0"

    elif norm_mode == "isolate":
        # Mid-channel isolation with vocal frequency bandpass (1200 Hz center, 2800 Hz bandwidth)
        return (
            f"stereotools=mlev=1.0:slev={MIN_STEREO_LEVEL:.6f},"
            "bandpass=frequency=1200:width_type=h:width=2800"
        )

    return None

