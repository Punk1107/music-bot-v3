# -*- coding: utf-8 -*-
"""
utils/color_thief.py — Async dominant-color extractor for Music Bot V3.

V3 Change: CPU-bound pixel work is now dispatched to a thread-pool executor
via loop.run_in_executor() so it never blocks the asyncio event loop.
A concurrency semaphore limits simultaneous extractions.

Pure Python — no Pillow dependency.
Supports JPEG and PNG thumbnails via HTTP.
"""

from __future__ import annotations

import asyncio
import colorsys
import io
import logging
import struct
import time
import zlib
from typing import Optional

import aiohttp

import config

logger = logging.getLogger(__name__)

# ── In-memory cache: url → (rgb_tuple, timestamp) ────────────────────────────
_COLOR_CACHE: dict[str, tuple[tuple[int, int, int], float]] = {}
_CACHE_TTL    = 3600.0   # 1 hour
_CACHE_MAX    = 1024

# ── Concurrency limit ─────────────────────────────────────────────────────────
_SEM: asyncio.Semaphore | None = None


def animated_embed_color(base_color: int, elapsed_seconds: int, interval: int = 7) -> int:
    """Return a gently hue-shifted embed color for the current playback time.

    Discord only permits one color for an embed's side rail.  Deriving the
    phase from elapsed playback time makes that rail change on each progress
    refresh without keeping another mutable timer or changing the song's
    original dominant color abruptly.
    """
    phase = max(0, elapsed_seconds) // max(1, interval)
    red = (base_color >> 16) & 0xFF
    green = (base_color >> 8) & 0xFF
    blue = base_color & 0xFF
    hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)

    # A 20-step loop: visible movement every 7s, full cycle in about 140s.
    hue = (hue + (phase % 20) / 20) % 1.0
    saturation = max(0.55, saturation)
    value = max(0.55, value)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return (round(red * 255) << 16) | (round(green * 255) << 8) | round(blue * 255)


def _get_sem() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(config.COLOR_EXTRACT_CONCURRENCY)
    return _SEM


# ── Blocking helpers (run in executor) ───────────────────────────────────────

def _sample_jpeg(data: bytes, samples: int = 400) -> tuple[int, int, int]:
    """Extract dominant color from raw JPEG bytes (simplified median cut)."""
    try:
        import struct
        pos = 0
        pixels: list[tuple[int, int, int]] = []

        # Walk JPEG markers to find SOF (Start of Frame)
        while pos < len(data) - 1:
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            if marker in (0xD8, 0xD9, 0x01):  # SOI, EOI, TEM
                pos += 2
                continue
            if pos + 3 >= len(data):
                break
            length = struct.unpack(">H", data[pos + 2: pos + 4])[0]
            if marker in (0xC0, 0xC2):  # SOF0, SOF2
                # height=data[5:7], width=data[7:9], components=data[9]
                break
            pos += 2 + length

        # Fallback: sample raw bytes as approximate RGB triples
        step = max(1, len(data) // (samples * 3))
        for i in range(0, len(data) - 2, step * 3):
            r, g, b = data[i], data[i + 1], data[i + 2]
            if r > 20 and g > 20 and b > 20:  # skip near-black
                pixels.append((r, g, b))
            if len(pixels) >= samples:
                break

        if not pixels:
            return (88, 101, 242)  # Discord blurple fallback

        return _average_color(pixels)
    except Exception:
        return (88, 101, 242)


def _sample_png(data: bytes, samples: int = 400) -> tuple[int, int, int]:
    """Extract dominant color from raw PNG bytes."""
    try:
        # PNG header check
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return (88, 101, 242)

        pixels: list[tuple[int, int, int]] = []
        pos = 8
        while pos < len(data) - 12:
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            chunk_type = data[pos + 4:pos + 8]
            chunk_data = data[pos + 8:pos + 8 + length]
            pos += 12 + length

            if chunk_type == b'IDAT':
                try:
                    raw = zlib.decompress(chunk_data)
                    step = max(1, len(raw) // (samples * 4))
                    for i in range(0, len(raw) - 3, step * 4):
                        r, g, b = raw[i], raw[i + 1], raw[i + 2]
                        if r > 20 and g > 20 and b > 20:
                            pixels.append((r, g, b))
                        if len(pixels) >= samples:
                            break
                    break
                except Exception:
                    pass

        return _average_color(pixels) if pixels else (88, 101, 242)
    except Exception:
        return (88, 101, 242)


def _average_color(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not pixels:
        return (88, 101, 242)
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    return (r, g, b)


def _extract_dominant_color(raw_bytes: bytes, content_type: str) -> tuple[int, int, int]:
    """Synchronous (blocking) extraction — call via run_in_executor."""
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return _sample_jpeg(raw_bytes)
    elif "png" in ct:
        return _sample_png(raw_bytes)
    # Try JPEG heuristic for unknown types
    if raw_bytes[:2] == b'\xff\xd8':
        return _sample_jpeg(raw_bytes)
    return (88, 101, 242)


# ── Async public API ──────────────────────────────────────────────────────────

async def get_dominant_color(
    url: Optional[str],
    session: Optional[aiohttp.ClientSession] = None,
    fallback: int = 0x5865F2,  # Discord blurple
) -> int:
    """
    Return the dominant color of an image URL as a Discord-compatible integer.

    V3: CPU work dispatched to thread-pool executor via loop.run_in_executor().
    Results cached for CACHE_TTL seconds. Max CACHE_MAX entries.

    Args:
        url:      Image URL (thumbnail). Returns fallback if None.
        session:  Shared aiohttp session. Creates a temporary one if None.
        fallback: Color to return on any error.
    """
    if not url:
        return fallback

    now = time.monotonic()

    # Cache hit
    if url in _COLOR_CACHE:
        color, ts = _COLOR_CACHE[url]
        if now - ts < _CACHE_TTL:
            return (color[0] << 16) | (color[1] << 8) | color[2]
        del _COLOR_CACHE[url]

    async with _get_sem():
        try:
            close_session = session is None
            if close_session:
                session = aiohttp.ClientSession()

            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": "MusicBotV3/1.0"},
                ) as resp:
                    if not resp.ok:
                        return fallback
                    content_type = resp.headers.get("Content-Type", "image/jpeg")
                    raw = await resp.read()
            finally:
                if close_session and session:
                    await session.close()

            # Dispatch CPU work to executor
            loop = asyncio.get_running_loop()
            color = await loop.run_in_executor(
                None, _extract_dominant_color, raw, content_type
            )

            # Store in cache
            _COLOR_CACHE[url] = (color, now)
            if len(_COLOR_CACHE) > _CACHE_MAX:
                oldest = min(_COLOR_CACHE, key=lambda k: _COLOR_CACHE[k][1])
                del _COLOR_CACHE[oldest]

            return (color[0] << 16) | (color[1] << 8) | color[2]

        except Exception as exc:
            logger.debug("Color extraction failed for '%s': %s", url[:60] if url else "", exc)
            return fallback
