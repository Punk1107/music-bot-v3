# -*- coding: utf-8 -*-
"""
config.py — Centralised configuration & logging setup for Music Bot V3.

All constants loaded from environment (.env).
Import this module first in every other module that needs settings.

V3 Changes:
  - Removed AUDIO_BACKEND (Lavalink removed entirely — FFmpeg only)
  - Removed NLU_BACKEND / external LLM keys — NLU now uses internal Regex engine
  - Added AUTO_PLAYLIST, AUTO_PLAYLIST_SIZE
  - Added API_SECRET for webserver REST API auth (optional)
  - Added IDLE_TIMEOUT per-guild default
  - Tightened type hints and validation
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()

# ── Discord ───────────────────────────────────────────────────────────────────
TOKEN: str = os.getenv("DISCORD_TOKEN", "")
APP_ID: int | None = int(os.getenv("APP_ID")) if os.getenv("APP_ID") else None

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Please configure your .env file."
    )

# ── Spotify (optional — graceful no-op if absent) ─────────────────────────────
SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/musicbot.db")

# ── Developer channels (error forwarding) ─────────────────────────────────────
# Supports multiple channel IDs separated by commas.
# e.g. DEV_LOG_CHANNEL_IDS=123456789,987654321
_raw_dev_channels: str = os.getenv("DEV_LOG_CHANNEL_IDS", "") or os.getenv("DEV_LOG_CHANNEL_ID", "")
DEV_LOG_CHANNEL_IDS: list[int] = [
    int(cid.strip())
    for cid in _raw_dev_channels.split(",")
    if cid.strip().isdigit()
]
# Backward-compat alias: first channel in the list (or None)
DEV_LOG_CHANNEL_ID: int | None = DEV_LOG_CHANNEL_IDS[0] if DEV_LOG_CHANNEL_IDS else None

# ── Auto-resume on startup ────────────────────────────────────────────────────
AUTO_RESUME: bool = os.getenv("AUTO_RESUME", "false").lower() == "true"

# ── Slash command sync ────────────────────────────────────────────────────────
# Set to true ONLY when adding/removing commands. Frequent sync → 429.
SYNC_COMMANDS: bool = os.getenv("SYNC_COMMANDS", "false").lower() == "true"

# ── Webserver ─────────────────────────────────────────────────────────────────
WEB_PORT: int = int(os.getenv("WEB_PORT", "8080"))
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
# Optional bearer token for REST API (leave empty to disable auth)
API_SECRET: str = os.getenv("API_SECRET", "")
# Per-IP request limit for REST API (requests per minute)
API_RATE_LIMIT: int = int(os.getenv("API_RATE_LIMIT", "60"))

# ── Audio (FFmpeg only — Lavalink removed in V3) ──────────────────────────────
YTDL_AUDIO_FORMAT: str = os.getenv("YTDL_AUDIO_FORMAT", "bestaudio[ext=webm]/bestaudio/best")
YTDL_RETRIES: int = int(os.getenv("YTDL_RETRIES", "3"))
YTDL_TIMEOUT: float = float(os.getenv("YTDL_TIMEOUT", "30.0"))
YTDL_STREAM_TIMEOUT: float = float(os.getenv("YTDL_STREAM_TIMEOUT", "20.0"))
YTDL_CACHE_TIMEOUT: float = float(os.getenv("YTDL_CACHE_TIMEOUT", "1800.0"))  # 30 min
YTDL_CACHE_MAX_SIZE: int = int(os.getenv("YTDL_CACHE_MAX_SIZE", "512"))
SEARCH_CACHE_TTL: float = float(os.getenv("SEARCH_CACHE_TTL", "600.0"))  # 10 min
SEARCH_CACHE_MAX_SIZE: int = int(os.getenv("SEARCH_CACHE_MAX_SIZE", "256"))
STREAM_URL_TTL: float = float(os.getenv("STREAM_URL_TTL", "14400.0"))   # 4 hours

# ── yt-dlp Cookie & PO-Token (VPS anti-blocking, Feature 1.6) ────────────────
# Point to a Netscape-format cookie file exported from your browser.
# Leave YTDL_COOKIE_FILE as "cookies.txt" (default); the file is loaded only
# when it actually exists on disk — safe to omit in local development.
YTDL_COOKIE_FILE: str = os.getenv("YTDL_COOKIE_FILE", "cookies.txt")
# Optional PO-Token for bypassing YouTube's bot detection.
# Generate with: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
YTDL_PO_TOKEN: str = os.getenv("YTDL_PO_TOKEN", "")

# ── Extraction concurrency ─────────────────────────────────────────────────────
# Caps simultaneous heavy yt-dlp calls to avoid CPU spikes.
EXTRACT_CONCURRENCY: int = int(os.getenv("EXTRACT_CONCURRENCY", "3"))

# ── Playlist extraction concurrency (Perf-1) ──────────────────────────────────
# Caps simultaneous bulk-playlist extraction jobs across all guilds.
# A 100-track playlist should not spawn 100 yt-dlp workers at once.
PLAYLIST_EXTRACT_CONCURRENCY: int = int(os.getenv("PLAYLIST_EXTRACT_CONCURRENCY", "2"))

# ── Color extraction concurrency ──────────────────────────────────────────────
COLOR_EXTRACT_CONCURRENCY: int = int(os.getenv("COLOR_EXTRACT_CONCURRENCY", "3"))

# ── Track limits ──────────────────────────────────────────────────────────────
MAX_TRACK_LENGTH: int = int(os.getenv("MAX_TRACK_LENGTH", "7200"))  # seconds (2h)
MAX_PLAYLIST_TRACKS: int = int(os.getenv("MAX_PLAYLIST_TRACKS", "100"))
MAX_QUEUE_SIZE: int = int(os.getenv("MAX_QUEUE_SIZE", "500"))

# ── Circuit Breaker ───────────────────────────────────────────────────────────
CIRCUIT_BREAKER_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
CIRCUIT_BREAKER_WINDOW: float = float(os.getenv("CIRCUIT_BREAKER_WINDOW", "60.0"))

# ── Voice reconnect ───────────────────────────────────────────────────────────
RECONNECT_ATTEMPTS: int = int(os.getenv("RECONNECT_ATTEMPTS", "3"))
RECONNECT_BASE_DELAY: float = float(os.getenv("RECONNECT_BASE_DELAY", "2.0"))

# ── Auto-disconnect ───────────────────────────────────────────────────────────
IDLE_TIMEOUT: int = int(os.getenv("IDLE_TIMEOUT", "300"))  # seconds (5 min)

# ── Auto-skip broken tracks ───────────────────────────────────────────────────
SKIP_ERROR_LIMIT: int = int(os.getenv("SKIP_ERROR_LIMIT", "5"))

# ── Auto-playlist (V3 NEW) ────────────────────────────────────────────────────
# When queue empties, pull from recent play history and enqueue automatically.
AUTO_PLAYLIST: bool = os.getenv("AUTO_PLAYLIST", "false").lower() == "true"
AUTO_PLAYLIST_SIZE: int = int(os.getenv("AUTO_PLAYLIST_SIZE", "5"))

# ── Smart Autoplay (Feature 1.4) ──────────────────────────────────────────────
# When queue empties, fetch YouTube Related Videos from the last-played track.
SMART_AUTOPLAY: bool = os.getenv("SMART_AUTOPLAY", "false").lower() == "true"
# How many related tracks to pre-fetch per seed video.
SMART_AUTOPLAY_FETCH_SIZE: int = int(os.getenv("SMART_AUTOPLAY_FETCH_SIZE", "8"))
# Rolling dedup history size — tracks already played are skipped.
SMART_AUTOPLAY_HISTORY_SIZE: int = int(os.getenv("SMART_AUTOPLAY_HISTORY_SIZE", "100"))

# ── Favorites limits (V3 NEW) ─────────────────────────────────────────────────
MAX_FAVORITES_PER_USER: int = int(os.getenv("MAX_FAVORITES_PER_USER", "50"))

# ── Queue persistence ─────────────────────────────────────────────────────────
QUEUE_SAVE_INTERVAL: int = int(os.getenv("QUEUE_SAVE_INTERVAL", "300"))  # seconds

# ── NLU (V3: Regex engine — no external API) ──────────────────────────────────
NLU_ENABLED: bool = os.getenv("NLU_ENABLED", "true").lower() == "true"



# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """
    Configure structured logging:
      - Coloured console handler (INFO+)
      - Rotating file: logs/bot.log (DEBUG+, 10 MB × 5 rotations)
      - Rotating file: logs/errors.log (ERROR+, 5 MB × 3 rotations)
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)-30s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console (INFO) ────────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    # ── Full rotating log (DEBUG) ─────────────────────────────────────────────
    full_log = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    full_log.setLevel(logging.DEBUG)
    full_log.setFormatter(fmt)

    # ── Error-only rotating log ───────────────────────────────────────────────
    error_log = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_log.setLevel(logging.ERROR)
    error_log.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(full_log)
    root.addHandler(error_log)

    # Silence noisy third-party loggers
    for noisy in ("discord", "discord.http", "discord.gateway", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
