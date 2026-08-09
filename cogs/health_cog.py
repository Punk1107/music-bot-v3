# -*- coding: utf-8 -*-
"""
cogs/health_cog.py — Health Report (Feature 35) for Music Bot V3.

Displays a comprehensive system status embed showing:
  - Voice connections status
  - SQLite database status
  - LRU Cache stats (metadata / thumbnail / search) — F31
  - Memory usage (RAM %) — F32
  - CPU usage
  - Reconnect / circuit-breaker status
  - Startup self-test results — F34
  - Bot uptime and active guilds

Commands:
  /health — Show full health report (Admin/DJ only)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.lru_cache import combined_stats as lru_combined_stats
from core import metrics as metrics_module
from utils.embeds import error_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


def _try_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _uptime_str(start_time: datetime) -> str:
    delta = datetime.now(timezone.utc) - start_time
    total = int(delta.total_seconds())
    d, r  = divmod(total, 86400)
    h, r  = divmod(r, 3600)
    m, s  = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _status_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


class HealthCog(commands.Cog, name="Health"):
    """Health Report commands (F35)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_dj(self, interaction: discord.Interaction) -> bool:
        try:
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
        except Exception:
            cfg = None
        if interaction.user.guild_permissions.administrator:
            return True
        if cfg and cfg.dj_role_id and any(r.id == cfg.dj_role_id for r in interaction.user.roles):
            return True
        await interaction.followup.send(
            embed=error_embed("Permission Denied", "Only **Admins** or **DJs** can view the health report."),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="health", description="Show bot health report (Admin/DJ only)")
    async def health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        psutil = _try_psutil()
        embed  = discord.Embed(
            title       = "🩺  Bot Health Report",
            description = f"Uptime: **{_uptime_str(self.bot.start_time)}**  •  Guilds: **{len(self.bot.guilds)}**",
            color       = 0x2ED573,
            timestamp   = datetime.now(timezone.utc),
        )

        # ── Voice Connections ──────────────────────────────────────────────────
        voice_guilds = [g for g in self.bot.guilds if g.voice_client and g.voice_client.is_connected()]
        active_vc    = len(voice_guilds)
        playing      = sum(1 for g in voice_guilds if g.voice_client.is_playing())
        embed.add_field(
            name   = "🎤  Voice",
            value  = f"**{active_vc}** connected  •  **{playing}** playing",
            inline = True,
        )

        # ── SQLite (live ping) ────────────────────────────────────────────────────
        try:
            db_latency = await asyncio.wait_for(self.bot.db.ping(), timeout=5.0)
            db_icon    = "✅" if db_latency < 100 else ("⚠️" if db_latency < 200 else "🔴")
            db_status  = f"{db_icon} {db_latency:.0f}ms ping"
        except asyncio.TimeoutError:
            db_status = "❌ Timed out (>5s)"
        except Exception as exc:
            db_status = f"❌ Error: {str(exc)[:60]}"

        embed.add_field(name="🗄️  SQLite",  value=db_status, inline=True)

        # ── Memory (psutil) ───────────────────────────────────────────────────
        # Shows TWO values:
        #   1. Bot process RSS  — primary metric, drives cache eviction thresholds
        #   2. System RAM       — diagnostic only (high system RAM ≠ bot memory issue)
        if psutil:
            import os as _os
            vm          = psutil.virtual_memory()
            sys_ram_pct = vm.percent
            sys_ram_used = vm.used  // (1024 * 1024)
            sys_ram_tot  = vm.total // (1024 * 1024)

            try:
                proc    = psutil.Process(_os.getpid())
                rss_mb  = proc.memory_info().rss / (1024 * 1024)
                # Thresholds match lru_cache.py defaults (overridable via config)
                try:
                    import config as _cfg
                    _light = float(getattr(_cfg, "MEMORY_PRESSURE_MB_LIGHT", 400))
                    _agg   = float(getattr(_cfg, "MEMORY_PRESSURE_MB_AGGRESSIVE", 900))
                except Exception:
                    _light, _agg = 400.0, 900.0
                bot_icon = "✅" if rss_mb < _light else ("⚠️" if rss_mb < _agg else "🔴")
                bot_mem_str = f"{bot_icon} **{rss_mb:.0f} MiB** (bot process RSS)"
            except Exception:
                bot_mem_str = "⚠️ process RSS unavailable"

            sys_icon = "✅" if sys_ram_pct < 70 else ("⚠️" if sys_ram_pct < 90 else "🔴")
            embed.add_field(
                name  = "🧠  Memory",
                value = (
                    f"{bot_mem_str}\n"
                    f"{sys_icon} System: **{sys_ram_pct:.1f}%** ({sys_ram_used}MB / {sys_ram_tot}MB) — diagnostic"
                ),
                inline = False,
            )
        else:
            embed.add_field(name="🧠  Memory", value="⚠️ psutil not installed", inline=True)

        # ── CPU ───────────────────────────────────────────────────────────────
        if psutil:
            # cpu_percent with interval=None is non-blocking (uses last measurement)
            cpu_pct  = psutil.cpu_percent(interval=None)
            cpu_icon = "✅" if cpu_pct < 60 else ("⚠️" if cpu_pct < 85 else "🔴")
            embed.add_field(name="⚙️  CPU", value=f"{cpu_icon} **{cpu_pct:.1f}%**", inline=True)
        else:
            embed.add_field(name="⚙️  CPU", value="⚠️ psutil not installed", inline=True)

        # ── LRU Cache Stats ───────────────────────────────────────────────────
        try:
            cache_data = await lru_combined_stats()
            cache_lines = []
            for name, s in cache_data.items():
                hit_rate = f"{s['hit_rate']*100:.0f}%"
                cache_lines.append(
                    f"**{name.title()}**: {s['size']}/{s['max_size']} entries  •  hit rate {hit_rate}"
                )
            cache_text = "\n".join(cache_lines) if cache_lines else "—"
        except Exception:
            cache_text = "❌ Error reading cache stats"

        embed.add_field(
            name   = "📦  LRU Caches",
            value  = cache_text,
            inline = False,
        )

        # ── Circuit Breakers ──────────────────────────────────────────────────
        yt_cb = self.bot.yt_breaker
        sp_cb = self.bot.sp_breaker
        yt_icon = "✅" if not yt_cb.is_open else "🔴"
        sp_icon = "✅" if not sp_cb.is_open else "🔴"
        embed.add_field(
            name  = "🔌  Circuit Breakers",
            value = (
                f"{yt_icon} YouTube: **{'OPEN (degraded)' if yt_cb.is_open else 'Closed'}**  "
                f"  {sp_icon} Spotify: **{'OPEN' if sp_cb.is_open else 'Closed'}**"
            ),
            inline = False,
        )

        # ── Startup Self-Test Results (F34) ───────────────────────────────────
        report = getattr(self.bot, "self_test_report", None)
        if report:
            lines = []
            for chk in report.checks:
                icon   = "✅" if chk.ok else "❌"
                detail = f" — {chk.detail[:60]}" if chk.detail else ""
                lat    = f" ({chk.latency*1000:.0f}ms)" if chk.latency else ""
                lines.append(f"{icon} **{chk.name}**{detail}{lat}")
            st_text = "\n".join(lines)
            st_text += f"\n*Ran {report.total_secs:.1f}s after startup*"
        else:
            st_text = "⏳ Self-test not yet complete"

        embed.add_field(
            name   = "🔍  Startup Self-Test",
            value  = st_text,
            inline = False,
        )

        embed.set_footer(text="Music Bot V3  •  /health")

        # ── Runtime Metrics Snapshot ──────────────────────────────────────────────────
        snap = metrics_module.get_latest_snapshot()
        if snap:
            ws_icon = "✅" if snap.ws_latency_ms < 150 else ("⚠️" if snap.ws_latency_ms < 300 else "🔴")
            metrics_val = (
                f"**Guilds:** {snap.guild_count}  •  "
                f"**Players:** {snap.active_players} active / {snap.voice_connections} in VC\n"
                f"**Queue:** {snap.total_queue_length} tracks total  •  "
                f"**Tasks:** {snap.asyncio_task_count}\n"
                f"{ws_icon} **WebSocket:** {snap.ws_latency_ms:.0f}ms  •  "
                f"**Last snapshot:** {snap.timestamp_iso()}"
            )
        else:
            metrics_val = "⏳ No snapshot yet (runs every 5 min)"

        embed.add_field(
            name   = "📊  Runtime Metrics",
            value  = metrics_val,
            inline = False,
        )

        # ── Memory Trend ────────────────────────────────────────────────────────────
        mem_detector = getattr(self.bot, "mem_leak_detector", None)
        if mem_detector and mem_detector.latest:
            latest  = mem_detector.latest
            history = mem_detector.history
            if len(history) >= 2:
                oldest      = history[0]
                rss_delta   = latest.rss_mb - oldest.rss_mb
                elapsed_min = (latest.ts - oldest.ts) / 60.0
                trend_icon  = "🔴" if rss_delta > 50 else ("⚠️" if rss_delta > 20 else "✅")
                trend_text  = (
                    f"{trend_icon} RSS **{latest.rss_mb:.0f}MiB**  "
                    f"(Δ{rss_delta:+.1f}MiB over {elapsed_min:.0f}min)\n"
                    f"Threads: **{latest.thread_count}**  •  "
                    f"asyncio tasks: **{latest.task_count}**  •  "
                    f"GC objects: **{latest.gc_objects:,}**"
                )
            else:
                trend_text = f"✅ RSS **{latest.rss_mb:.0f}MiB**  (only 1 snapshot so far)"
        else:
            trend_text = "⏳ No memory snapshots yet (runs every 30 min)"

        embed.add_field(
            name   = "🧠  Memory Trend (30-min window)",
            value  = trend_text,
            inline = False,
        )

        # ── Dead Task Watchdog ────────────────────────────────────────────────────────
        watchdog = getattr(self.bot, "task_watchdog", None)
        if watchdog:
            watched_tasks = getattr(watchdog, "_tasks", [])
            wt_lines = []
            for wt in watched_tasks:
                icon   = "✅" if wt.loop.is_running() else "🔴"
                rcount = f"  (restarted {wt.restart_count}×)" if wt.restart_count else ""
                wt_lines.append(f"{icon} **{wt.name}**{rcount}")
            wd_text = "\n".join(wt_lines) if wt_lines else "—"
        else:
            wd_text = "⏳ Watchdog not initialised"

        embed.add_field(
            name   = "🐕  Dead Task Watchdog",
            value  = wd_text,
            inline = False,
        )

        embed.set_footer(text="Music Bot V3  •  /health")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="cacheinfo", description="Show detailed LRU cache statistics (Admin/DJ)")
    async def cacheinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):

            return

        try:
            data = await lru_combined_stats()
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed("Cache Error", str(exc)), ephemeral=True
            )
            return

        embed = discord.Embed(title="📦  LRU Cache Statistics", color=0x70A1FF)
        for name, s in data.items():
            total    = s["hits"] + s["misses"]
            hit_rate = f"{s['hit_rate']*100:.1f}%"
            embed.add_field(
                name  = f"**{name.title()}** Cache",
                value = (
                    f"Size: `{s['size']}/{s['max_size']}`  TTL: `{int(s['ttl'])}s`\n"
                    f"Hits: `{s['hits']}`  Misses: `{s['misses']}`  Rate: `{hit_rate}`\n"
                    f"Sets: `{s['sets']}`  Evictions: `{s['evictions']}`"
                ),
                inline = False,
            )
        embed.set_footer(text="Use /health for full system status")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(HealthCog(bot))
