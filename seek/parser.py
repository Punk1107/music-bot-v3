# -*- coding: utf-8 -*-
"""
seek/parser.py — Timestamp parser for Music Bot V3 Feature 2.1.

Converts various human-readable time strings into total integer seconds.
Supports:
  - Pure seconds: '90', '90s', '120'
  - Colon notation (MM:SS): '1:30' -> 90s, '01:30' -> 90s, '0:45' -> 45s
  - Colon notation (HH:MM:SS): '1:05:30' -> 3930s, '01:00:00' -> 3600s
  - Text units: '1m 30s', '1m30s', '2m', '1h 10m', '45s'
"""

from __future__ import annotations

import re
from typing import Optional

_UNIT_RE = re.compile(
    r"^(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(\d+)\s*s(?:ec(?:onds?)?)?)?$"
)


def parse_time_string(time_str: str) -> Optional[int]:
    """
    Parse a user-supplied time string into an integer number of seconds.

    Returns:
        Non-negative integer seconds on success, or None if the format is invalid.
    """
    if not time_str or not isinstance(time_str, str):
        return None

    s = time_str.strip().lower()
    if not s or "-" in s:
        return None

    # Colon formats: MM:SS or HH:MM:SS
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            m_str, s_str = parts
            if not (m_str.isdigit() and s_str.isdigit()):
                return None
            m, sec = int(m_str), int(s_str)
            if sec >= 60:
                return None
            return m * 60 + sec
        elif len(parts) == 3:
            h_str, m_str, s_str = parts
            if not (h_str.isdigit() and m_str.isdigit() and s_str.isdigit()):
                return None
            h, m, sec = int(h_str), int(m_str), int(s_str)
            if m >= 60 or sec >= 60:
                return None
            return h * 3600 + m * 60 + sec
        else:
            return None

    # Text unit format: e.g. "1h 30m 15s", "1m30s", "45s", "2m"
    match = _UNIT_RE.match(s)
    if match and any(match.groups()):
        h_str, m_str, s_str = match.groups()
        h = int(h_str) if h_str else 0
        m = int(m_str) if m_str else 0
        sec = int(s_str) if s_str else 0
        return h * 3600 + m * 60 + sec

    # Pure integer seconds
    if s.isdigit():
        return int(s)

    return None
