# -*- coding: utf-8 -*-
"""
core/nlu.py — Regex-based Natural Language Understanding for Music Bot V3.

V3 BREAKING CHANGE: Completely replaced the OpenAI/Anthropic LLM pipeline
with a lightweight, zero-dependency regex intent engine.

Supports both English and Thai patterns.
No external API calls. No third-party AI dependencies.

Intent map:
  PLAY    → เล่น/play/หยุดแล้วเปิด ← followed by a query
  PAUSE   → หยุด/pause/พัก
  RESUME  → ต่อ/resume/เล่นต่อ
  SKIP    → ข้าม/skip/next/ต่อไป
  STOP    → หยุดเลย/stop/ออก/disconnect
  VOLUME  → เสียง/ดังขึ้น/เบาลง/volume/louder/quieter
  QUEUE   → คิว/queue/รายการ/list
  LOOP    → วนซ้ำ/loop/repeat
  SHUFFLE → สุ่ม/shuffle/random
  UNKNOWN → no match
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from models.enums import NLUIntent

logger = logging.getLogger(__name__)


# ─────────────────────── Intent patterns ─────────────────────────────────────

_INTENT_PATTERNS: list[tuple[NLUIntent, re.Pattern]] = [
    # ── PLAY (must come before RESUME / SKIP)
    (NLUIntent.PLAY,    re.compile(
        r"\b(play|เล่น|เปิด(?:เพลง)?|ขอฟัง|เปิด)\s*(?P<query>.+)",
        re.I | re.UNICODE,
    )),

    # ── PAUSE
    (NLUIntent.PAUSE,   re.compile(
        r"\b(pause|หยุด(?:ชั่วคราว)?|พัก|stop playing)\b",
        re.I | re.UNICODE,
    )),

    # ── RESUME
    (NLUIntent.RESUME,  re.compile(
        r"\b(resume|เล่นต่อ|ต่อ|ต่อเลย|unpause)\b",
        re.I | re.UNICODE,
    )),

    # ── SKIP
    (NLUIntent.SKIP,    re.compile(
        r"\b(skip|next|ข้าม|ต่อไป|เพลงต่อไป|ข้ามไป)\b",
        re.I | re.UNICODE,
    )),

    # ── STOP
    (NLUIntent.STOP,    re.compile(
        r"\b(stop|หยุดเลย|ออก|disconnect|หยุดหมด|ปิด(?:บอท)?)\b",
        re.I | re.UNICODE,
    )),

    # ── VOLUME (with optional number extraction)
    (NLUIntent.VOLUME,  re.compile(
        r"\b(volume|เสียง|ดังขึ้น|louder|เบาลง|quieter|ลดเสียง|เพิ่มเสียง|vol)\b",
        re.I | re.UNICODE,
    )),

    # ── QUEUE
    (NLUIntent.QUEUE,   re.compile(
        r"\b(queue|คิว|รายการ(?:เพลง)?|list|ดูคิว)\b",
        re.I | re.UNICODE,
    )),

    # ── LOOP
    (NLUIntent.LOOP,    re.compile(
        r"\b(loop|repeat|วนซ้ำ|วน|ซ้ำ)\b",
        re.I | re.UNICODE,
    )),

    # ── SHUFFLE
    (NLUIntent.SHUFFLE, re.compile(
        r"\b(shuffle|random|สุ่ม|สุ่มเพลง)\b",
        re.I | re.UNICODE,
    )),
]

# Volume adjustment patterns
_VOLUME_NUMBER_RE = re.compile(r"\b(\d{1,3})\b")
_VOLUME_UP_RE     = re.compile(r"\b(ดังขึ้น|louder|เพิ่มเสียง|volume up|vol up)\b", re.I | re.UNICODE)
_VOLUME_DOWN_RE   = re.compile(r"\b(เบาลง|quieter|ลดเสียง|volume down|vol down)\b", re.I | re.UNICODE)


@dataclass
class NLUResult:
    """Result of a single NLU parse."""
    intent:     NLUIntent
    query:      Optional[str] = None    # For PLAY intent: extracted search query
    volume:     Optional[int] = None    # For VOLUME intent: explicit level (0-200)
    volume_dir: Optional[str] = None    # "up" | "down" | None (for relative change)
    confidence: float          = 1.0    # Always 1.0 for regex — deterministic
    raw_text:   str            = ""


class RegexNLU:
    """
    Lightweight, zero-dependency intent classifier using compiled regex patterns.

    Designed as a drop-in replacement for the LLM-based NLU pipeline from V2.
    No network calls, no external dependencies, sub-millisecond latency.
    """

    def parse(self, text: str) -> NLUResult:
        """
        Parse *text* and return an NLUResult.

        Always returns a result — UNKNOWN intent if no pattern matches.
        """
        if not text or not text.strip():
            return NLUResult(intent=NLUIntent.UNKNOWN, raw_text=text)

        text = text.strip()

        for intent, pattern in _INTENT_PATTERNS:
            m = pattern.search(text)
            if m:
                result = NLUResult(intent=intent, raw_text=text)

                if intent == NLUIntent.PLAY:
                    query = m.group("query").strip() if m.lastgroup == "query" and m.group("query") else None
                    if not query:
                        # fallback: everything after the trigger word
                        query = text[m.end():].strip() or None
                    result.query = query if query else None

                elif intent == NLUIntent.VOLUME:
                    # Try to extract a specific number (0-200)
                    num_match = _VOLUME_NUMBER_RE.search(text)
                    if num_match:
                        result.volume = min(200, max(0, int(num_match.group(1))))
                    elif _VOLUME_UP_RE.search(text):
                        result.volume_dir = "up"
                    elif _VOLUME_DOWN_RE.search(text):
                        result.volume_dir = "down"

                logger.debug(
                    "NLU: '%s' → %s (query=%r, vol=%s, dir=%s)",
                    text[:60], intent.value, result.query, result.volume, result.volume_dir,
                )
                return result

        return NLUResult(intent=NLUIntent.UNKNOWN, raw_text=text)

    def is_music_query(self, text: str) -> bool:
        """
        Heuristic: is this bare text likely a song/artist search query?
        Used for the request channel feature — if user types something that
        doesn't look like a command, treat it as a /play query.
        """
        if not text or len(text.strip()) < 2:
            return False
        # Reject if it starts with / (it's a slash command)
        if text.strip().startswith("/"):
            return False
        # Accept if it looks like a URL
        if text.strip().startswith(("http://", "https://")):
            return True
        # Accept if it's reasonably short (< 200 chars) and has no bot prefix
        stripped = text.strip()
        return len(stripped) <= 200


# ── Singleton — NLUPipeline alias for backward compat ────────────────────────

class NLUPipeline(RegexNLU):
    """
    Alias for backward compatibility with V2 code that references NLUPipeline.
    Internally delegates to RegexNLU — no external API calls.
    """
    pass
