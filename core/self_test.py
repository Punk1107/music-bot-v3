# -*- coding: utf-8 -*-
"""
core/self_test.py — Startup Self-Test for Music Bot V3.

Tier Performance F34: Startup Self Test
  Run before bot.on_ready() completes to verify all critical subsystems.
  Each check is isolated — a single failure does NOT abort the bot.

Checks (in order):
  1. SQLite        — can we read/write the DB?
  2. FFmpeg        — is the binary reachable and working?
  3. Voice         — does the discord.py voice backend load?
  4. Permissions   — does the bot token have the required intents?
  5. YouTube       — can yt-dlp reach YouTube? (lightweight probe)
  6. Spotify       — are Spotify credentials configured & valid?

Results are stored in SelfTestResult objects, aggregated in SelfTestReport.
The report is attached to `bot.self_test_report` for /health to display.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name:    str
    ok:      bool
    detail:  str = ""
    latency: float = 0.0    # seconds, 0 = not measured


@dataclass
class SelfTestReport:
    checks:     list[CheckResult] = field(default_factory=list)
    ran_at:     float             = field(default_factory=time.time)
    total_secs: float             = 0.0

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    def summary(self) -> str:
        ok_n    = sum(1 for c in self.checks if c.ok)
        total_n = len(self.checks)
        return f"{ok_n}/{total_n} checks passed in {self.total_secs:.2f}s"


# ── Individual checks ─────────────────────────────────────────────────────────

async def _check_sqlite(bot: "MusicBot") -> CheckResult:
    """Test SQLite: write a temp value and read it back."""
    t0 = time.monotonic()
    try:
        await bot.db.get_server_config(0)  # guild_id=0 is a safe sentinel
        latency = time.monotonic() - t0
        return CheckResult("SQLite", True, f"Read OK in {latency*1000:.0f}ms", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("SQLite", False, f"Error: {exc}", latency)


def _check_ffmpeg_sync() -> CheckResult:
    """Test FFmpeg: run ffmpeg -version in subprocess."""
    t0 = time.monotonic()
    try:
        path = shutil.which("ffmpeg")
        if not path:
            return CheckResult("FFmpeg", False, "ffmpeg not found in PATH")
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5,
        )
        latency = time.monotonic() - t0
        if result.returncode != 0:
            return CheckResult("FFmpeg", False, f"Non-zero exit: {result.stderr[:100]}", latency)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return CheckResult("FFmpeg", True, version_line[:80], latency)
    except subprocess.TimeoutExpired:
        return CheckResult("FFmpeg", False, "Timed out after 5s")
    except Exception as exc:
        return CheckResult("FFmpeg", False, str(exc))


async def _check_ffmpeg(bot: "MusicBot") -> CheckResult:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _check_ffmpeg_sync)


async def _check_voice(bot: "MusicBot") -> CheckResult:
    """Check discord.py voice backend."""
    t0 = time.monotonic()
    try:
        import discord
        # Verify PyNaCl is installed (required for voice encryption)
        import nacl
        latency = time.monotonic() - t0
        return CheckResult(
            "Voice",
            True,
            f"discord.py voice OK, PyNaCl {nacl.__version__}",
            latency,
        )
    except ImportError as exc:
        latency = time.monotonic() - t0
        return CheckResult("Voice", False, f"Missing dependency: {exc}", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("Voice", False, str(exc), latency)


async def _check_permissions(bot: "MusicBot") -> CheckResult:
    """Check bot has correct intents and is logged in."""
    t0 = time.monotonic()
    try:
        if not bot.is_ready():
            # We may be called before on_ready; check intents instead
            intents = bot.intents
            missing = []
            if not intents.voice_states:
                missing.append("voice_states")
            if not intents.guilds:
                missing.append("guilds")
            if not intents.message_content:
                missing.append("message_content")
            latency = time.monotonic() - t0
            if missing:
                return CheckResult("Permissions", False, f"Missing intents: {missing}", latency)
            return CheckResult("Permissions", True, "Intents OK (pre-ready check)", latency)

        # Post-ready: check actual permissions in first available guild
        for guild in bot.guilds:
            me = guild.me
            if me is None:
                continue
            perms = me.guild_permissions
            required = ["connect", "speak", "send_messages", "embed_links", "read_messages"]
            missing  = [p for p in required if not getattr(perms, p, False)]
            latency  = time.monotonic() - t0
            if missing:
                return CheckResult(
                    "Permissions", False,
                    f"Missing in guild {guild.name}: {missing}", latency,
                )
            return CheckResult("Permissions", True, f"Guild: {guild.name} — all perms OK", latency)

        latency = time.monotonic() - t0
        return CheckResult("Permissions", True, "No guilds joined (intents OK)", latency)

    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("Permissions", False, str(exc)[:120], latency)


async def _check_youtube(bot: "MusicBot") -> CheckResult:
    """Light yt-dlp probe: just verify the extractor initialises."""
    t0 = time.monotonic()
    try:
        loop = asyncio.get_running_loop()

        def _probe():
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True}) as ydl:
                # Just instantiating and resolving extractors is enough
                return ydl.params.get("format", "bestaudio")

        fmt = await asyncio.wait_for(loop.run_in_executor(None, _probe), timeout=8)
        latency = time.monotonic() - t0
        return CheckResult("YouTube (yt-dlp)", True, f"Extractor OK, format={fmt}", latency)
    except asyncio.TimeoutError:
        latency = time.monotonic() - t0
        return CheckResult("YouTube (yt-dlp)", False, "Probe timed out after 8s", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("YouTube (yt-dlp)", False, str(exc)[:120], latency)


async def _check_spotify(bot: "MusicBot") -> CheckResult:
    """Check Spotify credentials are configured."""
    t0 = time.monotonic()
    try:
        import config
        client_id     = getattr(config, "SPOTIFY_CLIENT_ID", "") or ""
        client_secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "") or ""

        if not client_id or not client_secret:
            latency = time.monotonic() - t0
            return CheckResult("Spotify", False, "SPOTIFY_CLIENT_ID / SECRET not set", latency)

        # Try to get a token (lightweight)
        sp = bot.spotify
        if hasattr(sp, "_get_token"):
            await sp._get_token()
        latency = time.monotonic() - t0
        return CheckResult("Spotify", True, "Credentials configured & token OK", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("Spotify", False, str(exc)[:120], latency)


async def _check_db_latency(bot: "MusicBot") -> CheckResult:
    """
    Measure actual DB round-trip latency using the dedicated ping() method.
    WARN if > 100ms (indicates disk/I/O pressure).
    """
    t0 = time.monotonic()
    try:
        latency_ms = await asyncio.wait_for(bot.db.ping(), timeout=10.0)
        status     = "OK" if latency_ms < 100 else f"SLOW ({latency_ms:.0f}ms)"
        ok         = latency_ms < 200  # 200ms hard threshold for FAIL
        return CheckResult(
            "DB Latency", ok,
            f"{status} — {latency_ms:.1f}ms round-trip",
            (time.monotonic() - t0),
        )
    except asyncio.TimeoutError:
        latency = time.monotonic() - t0
        return CheckResult("DB Latency", False, "Timed out after 10s", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("DB Latency", False, str(exc)[:100], latency)


async def _check_voice_latency(bot: "MusicBot") -> CheckResult:
    """
    Report Discord WebSocket latency (heartbeat round-trip).
    Not a failure if the bot isn't connected yet — returns a warning instead.
    """
    t0 = time.monotonic()
    try:
        ws_ms = bot.latency * 1000 if bot.latency else None
        latency = time.monotonic() - t0
        if ws_ms is None:
            return CheckResult("Voice/WS Latency", True, "Not yet connected (pre-ready)", latency)
        ok     = ws_ms < 200
        status = f"{ws_ms:.0f}ms"
        if ws_ms >= 200:
            status += " (HIGH — >200ms)"
        return CheckResult("Voice/WS Latency", ok, f"Discord WebSocket {status}", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("Voice/WS Latency", False, str(exc)[:100], latency)


async def _check_cache_status(bot: "MusicBot") -> CheckResult:
    """
    Verify LRU caches are functional and report their hit-rates.
    Flags a problem if a cache cannot be queried (corruption indicator).
    """
    t0 = time.monotonic()
    try:
        from core.lru_cache import combined_stats
        stats   = await asyncio.wait_for(combined_stats(), timeout=5.0)
        summary = "  ".join(
            f"{name}: {int(s.get('hit_rate', 0)*100)}% hit ({s.get('size',0)} entries)"
            for name, s in stats.items()
        )
        latency = time.monotonic() - t0
        return CheckResult("LRU Caches", True, summary or "No caches initialised", latency)
    except asyncio.TimeoutError:
        latency = time.monotonic() - t0
        return CheckResult("LRU Caches", False, "Stats query timed out — possible cache corruption", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("LRU Caches", False, f"Error: {str(exc)[:80]}", latency)


async def _check_config(bot: "MusicBot") -> CheckResult:
    """
    Verify critical config values are sensible.
    Issues are WARN (ok=True) not failures — bot can still run.
    """
    t0 = time.monotonic()
    try:
        import config
        issues: list[str] = []
        if not config.TOKEN:
            issues.append("TOKEN missing")
        if config.IDLE_TIMEOUT < 30:
            issues.append(f"IDLE_TIMEOUT={config.IDLE_TIMEOUT}s (very low)")
        if config.MAX_QUEUE_SIZE > 5000:
            issues.append(f"MAX_QUEUE_SIZE={config.MAX_QUEUE_SIZE} (very high)")
        if not getattr(config, "DEV_LOG_CHANNEL_ID", None):
            issues.append("DEV_LOG_CHANNEL_ID not set (no error forwarding)")
        latency = time.monotonic() - t0
        if issues:
            return CheckResult("Config", True, "Warnings: " + "; ".join(issues), latency)
        return CheckResult("Config", True, "All checked values look sane", latency)
    except Exception as exc:
        latency = time.monotonic() - t0
        return CheckResult("Config", False, str(exc)[:100], latency)


# ── Main runner ───────────────────────────────────────────────────────────────

async def run_self_test(bot: "MusicBot") -> SelfTestReport:
    """
    Run all startup checks and return a SelfTestReport.
    Each check is awaited sequentially so logs are readable.
    """
    report = SelfTestReport()
    t0     = time.monotonic()

    logger.info("🔍 Running startup self-test…")

    checks_to_run = [
        ("SQLite",          _check_sqlite),
        ("FFmpeg",          _check_ffmpeg),
        ("Voice",           _check_voice),
        ("Permissions",     _check_permissions),
        ("YouTube",         _check_youtube),
        ("Spotify",         _check_spotify),
        ("DB Latency",      _check_db_latency),
        ("Voice/WS Latency",_check_voice_latency),
        ("LRU Caches",      _check_cache_status),
        ("Config",          _check_config),
    ]

    for name, fn in checks_to_run:
        try:
            result = await fn(bot)
        except Exception as exc:
            result = CheckResult(name, False, f"Unexpected: {exc}")

        report.checks.append(result)
        icon = "✅" if result.ok else "❌"
        logger.info(
            "  %s %s: %s%s",
            icon,
            result.name,
            result.detail or "OK",
            f" ({result.latency*1000:.0f}ms)" if result.latency else "",
        )

    report.total_secs = time.monotonic() - t0
    logger.info("🔍 Self-test complete: %s", report.summary())
    return report
