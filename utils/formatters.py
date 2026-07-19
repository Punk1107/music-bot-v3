# -*- coding: utf-8 -*-
"""utils/formatters.py — String and time formatting helpers for Music Bot V3."""

from __future__ import annotations

from datetime import datetime, timezone


def format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    secs = max(0, int(seconds))
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_size(n: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def make_progress_bar(fraction: float, width: int = 15) -> str:
    """
    Create a Unicode block progress bar.

    Args:
        fraction: 0.0 – 1.0 completion.
        width:    Total bar width in characters.

    Returns:
        e.g. "████████░░░░░░░"
    """
    fraction = max(0.0, min(1.0, fraction))
    filled   = round(fraction * width)
    empty    = width - filled
    return "█" * filled + "░" * empty


def format_uptime(start_time: datetime) -> str:
    """Format bot uptime from start_time to now."""
    delta   = datetime.now(timezone.utc) - start_time
    total_s = int(delta.total_seconds())
    h, rem  = divmod(total_s, 3600)
    m, s    = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def truncate(s: str, max_len: int = 100, suffix: str = "…") -> str:
    """Truncate a string to max_len chars, appending suffix if cut."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def number_emoji(n: int) -> str:
    """Return a numbered emoji for display (1–10)."""
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
               "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    if 1 <= n <= 10:
        return emojis[n - 1]
    return str(n)
