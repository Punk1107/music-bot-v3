# -*- coding: utf-8 -*-
"""
core/validator.py — 7-stage URL safety pipeline + search sanitisation.

Stage 1: Pattern blacklist (regex)
Stage 2: Blacklisted domain exact-match
Stage 3: Blacklisted TLD match
Stage 4: Provider whitelist (YouTube, Spotify)
Stage 5: Audio file extension check
Stage 6: Async content-type sniffing (HEAD request)
Stage 7: Search query sanitisation (strip NSFW/harmful terms)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# ── Stage 1: URL pattern blacklist ────────────────────────────────────────────

_PATTERN_BLACKLIST = [
    re.compile(r"\b(porn|xxx|hentai|sex|nude|nsfw|onlyfans|xvideos|pornhub)\b", re.I),
    re.compile(r"\b(gambling|casino|poker|slots|bet365|betway)\b", re.I),
    re.compile(r"\b(drug|cocaine|heroin|methamphetamine|fentanyl)\b", re.I),
    re.compile(r"\b(piracy|torrent|warez|nulled|cracked)\b", re.I),
    re.compile(r"\b(โป๊|หนังโป๊|เสียว|อีโรติก|ลามก)\b", re.I | re.UNICODE),
    re.compile(r"\b(การพนัน|บาคาร่า|สล็อต|เดิมพัน)\b", re.I | re.UNICODE),
]

# ── Stage 2: Domain blacklist ─────────────────────────────────────────────────

_DOMAIN_BLACKLIST = frozenset([
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com",
    "redtube.com", "onlyfans.com", "chaturbate.com",
    "bet365.com", "betway.com", "pokerstars.com",
])

# ── Stage 3: TLD blacklist ────────────────────────────────────────────────────

_TLD_BLACKLIST = frozenset([".xxx", ".adult", ".sex", ".porn"])

# ── Stage 4: Provider whitelist ───────────────────────────────────────────────

_PROVIDER_WHITELIST = frozenset([
    "youtube.com", "youtu.be", "music.youtube.com",
    "m.youtube.com", "open.spotify.com",
    "soundcloud.com", "bandcamp.com",
])

# ── Stage 5: Audio extensions whitelist ───────────────────────────────────────

_AUDIO_EXTENSIONS = frozenset([
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma",
    ".opus", ".webm", ".mp4",
])

# ── Search query word blacklist ───────────────────────────────────────────────

_SEARCH_BLACKLIST = [
    re.compile(r"\b(porn|xxx|sex|nude|hentai|nsfw)\b", re.I),
    re.compile(r"\b(โป๊|เสียว|ลามก)\b", re.I | re.UNICODE),
]


def validate_search_query(query: str) -> tuple[bool, str]:
    """
    Stage 7: Sanitise a search query.
    Returns (is_safe, reason_if_unsafe).
    """
    for pattern in _SEARCH_BLACKLIST:
        if pattern.search(query):
            return False, "Search query contains inappropriate content."
    return True, ""


async def validate_url(
    url: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> tuple[bool, str]:
    """
    Run a URL through all 7 stages.
    Returns (is_safe, reason_if_blocked).

    Args:
        url:     The URL to validate.
        session: Optional shared aiohttp session for Stage 6 content-type check.
    """
    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL."

    netloc = parsed.netloc.lower()
    path   = parsed.path.lower()

    # Stage 1: Pattern blacklist
    for pattern in _PATTERN_BLACKLIST:
        if pattern.search(url):
            return False, "URL contains blacklisted content pattern."

    # Stage 2: Domain blacklist
    domain = netloc.lstrip("www.")
    if domain in _DOMAIN_BLACKLIST:
        return False, f"Domain '{domain}' is not allowed."

    # Stage 3: TLD blacklist
    for tld in _TLD_BLACKLIST:
        if netloc.endswith(tld):
            return False, f"Top-level domain '{tld}' is not allowed."

    # Stage 4: Provider whitelist (if recognised, skip further checks)
    for provider in _PROVIDER_WHITELIST:
        if domain == provider or domain.endswith(f".{provider}"):
            return True, ""

    # Stage 5: Audio extension check (allow direct audio file URLs)
    for ext in _AUDIO_EXTENSIONS:
        if path.endswith(ext):
            return True, ""

    # Stage 6: Async content-type sniffing
    if session:
        try:
            async with asyncio.timeout(5.0):
                async with session.head(url, allow_redirects=True) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    if any(t in ct for t in ("audio/", "video/", "application/ogg")):
                        return True, ""
        except Exception:
            pass

    # Default: unknown URL, block for safety
    return False, "URL is not from a recognised or allowed source."
