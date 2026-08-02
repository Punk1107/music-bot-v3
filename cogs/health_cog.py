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

        # ── SQLite ────────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            await self.bot.db.get_server_config(interaction.guild_id)
            db_latency = (time.monotonic() - t0) * 1000
            db_status  = f"✅ OK  ({db_latency:.0f}ms)"
            db_color   = "✅"
        except Exception as exc:
            db_status  = f"❌ Error: {str(exc)[:60]}"
            db_color   = "❌"

        embed.add_field(name="🗄️  SQLite",  value=db_status, inline=True)

        # ── Memory (psutil) ───────────────────────────────────────────────────
        if psutil:
            vm       = psutil.virtual_memory()
            ram_pct  = vm.percent
            ram_used = vm.used  // (1024 * 1024)
            ram_tot  = vm.total // (1024 * 1024)
            mem_icon = "✅" if ram_pct < 70 else ("⚠️" if ram_pct < 85 else "🔴")
            embed.add_field(
                name   = "🧠  Memory",
                value  = f"{mem_icon} **{ram_pct:.1f}%** ({ram_used}MB / {ram_tot}MB)",
                inline = True,
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
