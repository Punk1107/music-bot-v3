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


def make_progress_bar(fraction: float, url: str = "", width: int = 17) -> str:
    """
    Solid two-tone progress bar using block characters (no playhead knob).

    When *url* is supplied the filled segment is wrapped in a Markdown hyperlink —
    Discord renders it in link/accent colour, giving a clean two-tone look:

        "[▓▓▓▓▓▓▓](url)░░░░░░░░░░"

    Args:
        fraction: 0.0 – 1.0 completion.
        url:      Track URL for the colour trick (empty → plain single-colour bar).
        width:    Total number of block characters.
    """
    fraction   = max(0.0, min(1.0, fraction))
    filled     = round(fraction * width)
    empty      = width - filled
    filled_str = "▓" * filled
    empty_str  = "░" * empty

    if url and filled > 0:
        # Filled segment wrapped in a hyperlink → Discord accent colour
        return f"[{filled_str}]({url}){empty_str}"
    # No URL or nothing played yet — plain single colour
    return f"{filled_str}{empty_str}"


def make_knob_progress_bar(
    fraction:    float,
    elapsed_str: str,
    total_str:   str,
    paused:      bool = False,
    width:       int  = 17,
    url:         str  = "",
) -> str:
    """
    Spotify/music-player style progress bar with a round knob (●).

    Example output (with url):
        ▶ [──────●](url)──────────── [1:23/3:31] 🔊

    The filled segment + knob are wrapped in a Markdown hyperlink so
    Discord renders them in the accent/link colour — giving a two-tone look.

    Args:
        fraction:    0.0 – 1.0 completion.
        elapsed_str: Human-readable elapsed time (e.g. "1:23").
        total_str:   Human-readable total time   (e.g. "3:31").
        paused:      If True, use ⏸ prefix instead of ▶.
        width:       Total track width (─ chars + knob).
        url:         Track URL for the colour trick (empty → plain single-colour bar).
    """
    fraction  = max(0.0, min(1.0, fraction))
    knob_pos  = round(fraction * (width - 1))
    before    = "─" * knob_pos
    after     = "─" * (width - 1 - knob_pos)
    prefix    = "⏸️" if paused else "▶️"

    if url and knob_pos > 0:
        # Filled segment + knob wrapped in hyperlink → Discord accent/link colour
        filled_part = f"[{before}●]({url})"
    elif url:
        # Nothing played yet — knob at start, no filled segment to linkify
        filled_part = "●"
    else:
        filled_part = f"{before}●"

    return f"{prefix} {filled_part}{after} [{elapsed_str}/{total_str}] 🔉"


def format_views(n: int) -> str:
    """Format a view count as a compact human-readable string (e.g. 1.2M, 200.3K)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


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
