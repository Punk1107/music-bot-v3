# -*- coding: utf-8 -*-
"""
lyrics/parser.py — WebVTT / SRT subtitle parser for Music Bot V3 Feature 1.1.

Handles:
  - WebVTT (.vtt) format — primary format from YouTube
  - SRT (.srt) format   — fallback
  - Cleans HTML/WebVTT inline timing tags (auto-caption rolling captions)
  - Returns a list of LyricLine(start_sec, timestamp_str, text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class LyricLine:
    """A single lyric line with its timestamp and display text."""
    start_sec:     float
    timestamp_str: str    # "MM:SS" or "HH:MM:SS"
    text:          str


# ── Regex patterns ──────────────────────────────────────────────────────────

# VTT timestamp line: "00:01:23.456 --> 00:01:28.789" (with optional metadata)
_VTT_TIMESTAMP = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d+)\s*-->"
    r"\s*\d{1,2}:\d{2}:\d{2}[.,]\d+"
)
# Short-form VTT timestamp (no hours): "01:23.456 --> ..."
_VTT_TIMESTAMP_SHORT = re.compile(
    r"(\d{1,2}):(\d{2})[.,](\d+)\s*-->"
    r"\s*\d{1,2}:\d{2}[.,]\d+"
)

# SRT timestamp: "00:01:23,456 --> 00:01:28,789"
_SRT_TIMESTAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->"
    r"\s*\d{2}:\d{2}:\d{2},\d{3}"
)

# Inline VTT tags: <c>, </c>, <00:00:00.000>, <b>, </b>, <i>, </i>, etc.
_VTT_INLINE_TAGS = re.compile(r"<[^>]+>")

# HTML entities
_HTML_ENTITIES = [
    (re.compile(r"&amp;"),   "&"),
    (re.compile(r"&lt;"),    "<"),
    (re.compile(r"&gt;"),    ">"),
    (re.compile(r"&quot;"),  '"'),
    (re.compile(r"&#39;"),   "'"),
    (re.compile(r"&nbsp;"),  " "),
]

# VTT header / metadata junk
_VTT_HEADER      = re.compile(r"^WEBVTT.*$", re.MULTILINE)
_VTT_NOTE        = re.compile(r"^NOTE.*?(?=\n\n|\Z)", re.DOTALL | re.MULTILINE)
_VTT_STYLE       = re.compile(r"^STYLE.*?(?=\n\n|\Z)", re.DOTALL | re.MULTILINE)
_VTT_REGION      = re.compile(r"^REGION.*?(?=\n\n|\Z)", re.DOTALL | re.MULTILINE)
_SEQUENCE_NUMBER = re.compile(r"^\d+\s*$", re.MULTILINE)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Strip VTT inline tags and HTML entities from a text block."""
    text = _VTT_INLINE_TAGS.sub("", raw)
    for pattern, replacement in _HTML_ENTITIES:
        text = pattern.sub(replacement, text)
    return text.strip()


def _seconds(h: int, m: int, s: int, ms_str: str = "0") -> float:
    ms = int(ms_str[:3].ljust(3, "0"))   # normalise to milliseconds
    return h * 3600 + m * 60 + s + ms / 1000.0


def _format_ts(total_sec: float) -> str:
    """Format float seconds to MM:SS or HH:MM:SS string."""
    sec  = int(total_sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _deduplicate(lines: list[LyricLine]) -> list[LyricLine]:
    """
    Remove rolling-caption duplicates that YouTube auto-captions produce.

    YouTube ASR captions often emit the same sentence repeatedly, growing
    by one word each cue.  We keep only the *longest* version of any text
    that is a suffix of the next line.
    """
    if len(lines) <= 1:
        return lines

    out: list[LyricLine] = []
    i = 0
    while i < len(lines):
        current = lines[i]
        # Look ahead: if the next line's text starts with (or is) our text, skip us
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.text.startswith(current.text) and len(nxt.text) > len(current.text):
                i += 1
                continue
        out.append(current)
        i += 1

    # Second pass: collapse identical adjacent lines
    deduped: list[LyricLine] = []
    for line in out:
        if deduped and deduped[-1].text == line.text:
            continue
        deduped.append(line)

    return deduped


def _merge_short_lines(lines: list[LyricLine], min_words: int = 2) -> list[LyricLine]:
    """
    Merge very short 1-word cues into the following line.
    This handles cases where auto-captions split a word across two cues.
    """
    if not lines:
        return lines

    merged: list[LyricLine] = []
    pending: Optional[LyricLine] = None

    for line in lines:
        if pending is None:
            word_count = len(line.text.split())
            if word_count < min_words:
                pending = line
            else:
                merged.append(line)
        else:
            combined = LyricLine(
                start_sec     = pending.start_sec,
                timestamp_str = pending.timestamp_str,
                text          = f"{pending.text} {line.text}".strip(),
            )
            merged.append(combined)
            pending = None

    if pending:
        if merged:
            merged[-1] = LyricLine(
                start_sec     = merged[-1].start_sec,
                timestamp_str = merged[-1].timestamp_str,
                text          = f"{merged[-1].text} {pending.text}".strip(),
            )
        else:
            merged.append(pending)

    return merged


# ── Public API ───────────────────────────────────────────────────────────────

def parse_vtt(vtt_text: str) -> list[LyricLine]:
    """
    Parse a WebVTT string into a list of LyricLine objects.

    Steps:
      1. Strip header / NOTE / STYLE / REGION blocks
      2. Split into cue blocks
      3. Match timestamp line
      4. Clean inline tags
      5. Deduplicate rolling ASR captions
    """
    # 1. Strip header junk
    text = _VTT_HEADER.sub("", vtt_text)
    text = _VTT_NOTE.sub("",   text)
    text = _VTT_STYLE.sub("",  text)
    text = _VTT_REGION.sub("", text)
    text = _SEQUENCE_NUMBER.sub("", text)

    lines: list[LyricLine] = []

    # 2. Split on blank lines to get cue blocks
    blocks = re.split(r"\n{2,}", text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        block_lines = block.splitlines()

        # 3. Find the timestamp line (may not be the first line in block)
        ts_line_idx = -1
        ts_sec: Optional[float] = None
        for idx, bl in enumerate(block_lines):
            # Try long-form HH:MM:SS
            m = _VTT_TIMESTAMP.match(bl)
            if m:
                h, mm, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
                ts_sec      = _seconds(h, mm, s, ms)
                ts_line_idx = idx
                break
            # Try short-form MM:SS
            m = _VTT_TIMESTAMP_SHORT.match(bl)
            if m:
                mm, s, ms  = int(m.group(1)), int(m.group(2)), m.group(3)
                ts_sec      = _seconds(0, mm, s, ms)
                ts_line_idx = idx
                break

        if ts_sec is None or ts_line_idx < 0:
            continue

        # 4. Text is everything after the timestamp line
        text_parts = block_lines[ts_line_idx + 1:]
        raw_text   = " ".join(text_parts)
        cleaned    = _clean_text(raw_text)
        if not cleaned:
            continue

        lines.append(LyricLine(
            start_sec     = ts_sec,
            timestamp_str = _format_ts(ts_sec),
            text          = cleaned,
        ))

    # 5. Deduplicate & clean up
    lines = _deduplicate(lines)
    lines = _merge_short_lines(lines)

    return lines


def parse_srt(srt_text: str) -> list[LyricLine]:
    """
    Parse an SRT string into a list of LyricLine objects.
    Fallback for cases where VTT is unavailable.
    """
    lines: list[LyricLine] = []
    blocks = re.split(r"\n{2,}", srt_text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        block_lines = block.splitlines()

        ts_sec: Optional[float] = None
        ts_line_idx = -1
        for idx, bl in enumerate(block_lines):
            m = _SRT_TIMESTAMP.match(bl)
            if m:
                h, mm, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
                ts_sec       = _seconds(h, mm, s, ms)
                ts_line_idx  = idx
                break

        if ts_sec is None:
            continue

        text_parts = block_lines[ts_line_idx + 1:]
        raw_text   = " ".join(text_parts)
        cleaned    = _clean_text(raw_text)
        if not cleaned:
            continue

        lines.append(LyricLine(
            start_sec     = ts_sec,
            timestamp_str = _format_ts(ts_sec),
            text          = cleaned,
        ))

    lines = _deduplicate(lines)
    return lines


def find_current_line_index(lines: list[LyricLine], elapsed_sec: float) -> int:
    """
    Binary-search for the lyric line that is currently playing.
    Returns 0 if elapsed_sec is before the first line.
    """
    if not lines:
        return 0
    lo, hi = 0, len(lines) - 1
    result  = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid].start_sec <= elapsed_sec:
            result = mid
            lo     = mid + 1
        else:
            hi = mid - 1
    return result
