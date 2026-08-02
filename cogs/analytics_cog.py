# -*- coding: utf-8 -*-
"""
cogs/analytics_cog.py — Tier Analytics Dashboard for Music Bot V3.

Commands (/analytics group):
  /analytics heatmap  — Day-of-week x hour heatmap of listening activity
  /analytics genre    — Genre distribution inferred from metadata (no AI)
  /analytics peak     — Top peak listening hours with bar chart
  /analytics top      — Top Artists / Top Channels leaderboard
  /analytics streak   — Listening streak tracker (7/15/30 days)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


# ── Render helpers ─────────────────────────────────────────────────────────────

def _bar(value: int, max_val: int, width: int = 10) -> str:
    """Render a Unicode block progress bar."""
    if max_val == 0:
        return "░" * width
    filled = round((value / max_val) * width)
    return "█" * filled + "░" * (width - filled)


def _heat_cell(value: int, max_val: int) -> str:
    """Map a play count to a heat emoji."""
    if max_val == 0 or value == 0:
        return "⬛"
    ratio = value / max_val
    if ratio >= 0.75:
        return "🟥"
    if ratio >= 0.50:
        return "🟧"
    if ratio >= 0.25:
        return "🟨"
    return "🟩"


def _medal(n: int) -> str:
    medals = ["🥇", "🥈", "🥉"]
    return medals[n] if n < 3 else f"`#{n + 1}`"


def _hour_fmt(h: int) -> str:
    return f"{h:02d}:00"


# ── Analytics Cog ──────────────────────────────────────────────────────────────

class AnalyticsCog(commands.Cog, name="Analytics"):
    """Tier Analytics — deep listening statistics for your server."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    analytics = app_commands.Group(
        name        = "analytics",
        description = "📊 Tier Analytics — listening insights for your server",
    )

    # ── /analytics heatmap ────────────────────────────────────────────────────

    @analytics.command(
        name        = "heatmap",
        description = "🌡️ Heatmap of listening activity by day-of-week and hour",
    )
    @app_commands.describe(days="Look-back window in days (default: 30)")
    async def analytics_heatmap(
        self,
        interaction: discord.Interaction,
        days: int = 30,
    ) -> None:
        await interaction.response.defer()

        days = max(1, min(days, 365))
        heatmap = await self.bot.db.get_analytics_heatmap(interaction.guild_id, days)

        # Find global max for scaling
        all_vals = [v for row in heatmap.values() for v in row]
        max_val  = max(all_vals) if all_vals else 1

        # Hour markers every 6 hours
        hour_header = "     " + "".join(
            f"`{h:02d}`" if h % 6 == 0 else "    "
            for h in range(24)
        )

        lines: list[str] = []
        days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for day in days_order:
            row_data = heatmap[day]
            cells    = "".join(_heat_cell(v, max_val) for v in row_data)
            total    = sum(row_data)
            lines.append(f"`{day}` {cells} `{total:>3}`")

        legend = "⬛ None  🟩 Low  🟨 Mid  🟧 High  🟥 Peak"

        embed = discord.Embed(
            title       = "🌡️  Listening Heatmap",
            description = (
                f"Activity over the last **{days} days** · each cell = 1 hour\n\n"
                + hour_header + "\n"
                + "\n".join(lines)
                + f"\n\n{legend}"
            ),
            color = 0xFF6B6B,
        )
        embed.set_footer(text=f"📊 Tier Analytics · {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /analytics genre ──────────────────────────────────────────────────────

    @analytics.command(
        name        = "genre",
        description = "🎼 Genre distribution inferred from track metadata",
    )
    @app_commands.describe(days="Look-back window in days (default: 30)")
    async def analytics_genre(
        self,
        interaction: discord.Interaction,
        days: int = 30,
    ) -> None:
        await interaction.response.defer()

        days   = max(1, min(days, 365))
        genres = await self.bot.db.get_analytics_genre(interaction.guild_id, days)

        if not genres:
            embed = discord.Embed(
                title       = "🎼 Genre Statistics",
                description = "No play history found. Start listening to see genre stats!",
                color       = 0x95A5A6,
            )
            await interaction.followup.send(embed=embed)
            return

        total   = sum(g["count"] for g in genres)
        max_cnt = genres[0]["count"] if genres else 1

        _EMOJI: dict[str, str] = {
            "Lo-fi":     "☕", "Hip-Hop":  "🎤", "Pop":       "🌟",
            "Rock":      "🎸", "EDM":      "⚡", "Jazz":      "🎷",
            "Classical": "🎻", "R&B":      "💜", "Country":   "🤠",
            "Anime":     "✨", "Gaming":   "🎮", "Other":     "🎵",
        }

        lines: list[str] = []
        for i, g in enumerate(genres[:12]):
            emoji = _EMOJI.get(g["genre"], "🎵")
            pct   = (g["count"] / total * 100) if total else 0
            bar   = _bar(g["count"], max_cnt, width=12)
            medal = _medal(i)
            lines.append(
                f"{medal} {emoji} **{g['genre']}**\n"
                f"    `{bar}` {g['count']} plays ({pct:.1f}%)"
            )

        embed = discord.Embed(
            title       = "🎼  Genre Statistics",
            description = (
                f"Analysed **{total}** plays over **{days} days**\n"
                "*(Inferred from title & uploader metadata)*\n\n"
                + "\n".join(lines)
            ),
            color = 0xA29BFE,
        )
        embed.set_footer(text=f"📊 Tier Analytics · {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /analytics peak ───────────────────────────────────────────────────────

    @analytics.command(
        name        = "peak",
        description = "⏰ Peak listening hours — when your server is most active",
    )
    @app_commands.describe(days="Look-back window in days (default: 30)")
    async def analytics_peak(
        self,
        interaction: discord.Interaction,
        days: int = 30,
    ) -> None:
        await interaction.response.defer()

        days  = max(1, min(days, 365))
        hours = await self.bot.db.get_analytics_peak_hours(interaction.guild_id, days)

        if not hours:
            embed = discord.Embed(
                title       = "⏰ Peak Listening Hours",
                description = "No play history found yet!",
                color       = 0x95A5A6,
            )
            await interaction.followup.send(embed=embed)
            return

        max_plays = hours[0]["plays"] if hours else 1
        total     = sum(h["plays"] for h in hours)

        # Top 3 highlights
        top_section: list[str] = []
        for i, h in enumerate(hours[:3]):
            end_hour = (h["hour"] + 1) % 24
            medal    = _medal(i)
            pct      = h["plays"] / total * 100 if total else 0
            top_section.append(
                f"{medal} **{_hour_fmt(h['hour'])} – {_hour_fmt(end_hour)}**  "
                f"`{h['plays']} plays` ({pct:.1f}%)"
            )

        # Full 24h chart, 2 columns
        by_hour = {h["hour"]: h["plays"] for h in hours}
        chart_lines: list[str] = []
        for h in range(0, 24, 2):
            lp = by_hour.get(h, 0)
            rp = by_hour.get(h + 1, 0)
            chart_lines.append(
                f"`{h:02d}h` `{_bar(lp, max_plays, 8)}` `{lp:>3}`  "
                f"`{h+1:02d}h` `{_bar(rp, max_plays, 8)}` `{rp:>3}`"
            )

        embed = discord.Embed(
            title       = "⏰  Peak Listening Hours",
            description = (
                f"**{total}** total plays · last **{days} days**\n\n"
                "**🏆 Top Peak Times**\n"
                + "\n".join(top_section)
                + "\n\n**📊 Hourly Breakdown**\n"
                + "\n".join(chart_lines)
            ),
            color = 0xFD79A8,
        )
        embed.set_footer(text=f"📊 Tier Analytics · {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /analytics top ────────────────────────────────────────────────────────

    @analytics.command(
        name        = "top",
        description = "🎤 Top Artists and Top Channels leaderboard",
    )
    @app_commands.describe(
        days  = "Look-back window in days (default: 30)",
        limit = "Number of entries to show (default: 10, max: 15)",
    )
    async def analytics_top(
        self,
        interaction: discord.Interaction,
        days:  int = 30,
        limit: int = 10,
    ) -> None:
        await interaction.response.defer()

        days  = max(1, min(days, 365))
        limit = max(1, min(limit, 15))
        data  = await self.bot.db.get_analytics_top_entities(
            interaction.guild_id, days, limit
        )

        artists  = data.get("artists",  [])
        channels = data.get("channels", [])

        if not artists:
            embed = discord.Embed(
                title       = "🎤 Top Artists",
                description = "No play history found yet. Start listening!",
                color       = 0x95A5A6,
            )
            await interaction.followup.send(embed=embed)
            return

        max_plays = artists[0]["plays"] if artists else 1

        art_lines: list[str] = []
        for i, a in enumerate(artists):
            bar      = _bar(a["plays"], max_plays, width=10)
            minutes  = a.get("minutes", 0)
            time_str = f"{minutes}min" if minutes < 60 else f"{minutes // 60}h {minutes % 60}min"
            art_lines.append(
                f"{_medal(i)} **{discord.utils.escape_markdown(a['name'][:30])}**\n"
                f"    `{bar}` {a['plays']} plays · ⏱ {time_str}"
            )

        ch_lines: list[str] = []
        for i, c in enumerate(channels[:5]):
            ch_lines.append(
                f"{_medal(i)} **{discord.utils.escape_markdown(c['name'][:30])}** "
                f"— {c['plays']} plays"
            )

        embed = discord.Embed(
            title = "🎤  Top Artists & Channels",
            color = 0x6C5CE7,
        )
        embed.add_field(
            name   = f"🎵 Top Artists — last {days} days",
            value  = "\n".join(art_lines) or "*No data*",
            inline = False,
        )
        embed.add_field(
            name   = "📺 Top Channels (Top 5)",
            value  = "\n".join(ch_lines) or "*No data*",
            inline = False,
        )
        embed.set_footer(text=f"📊 Tier Analytics · {interaction.guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /analytics streak ─────────────────────────────────────────────────────

    @analytics.command(
        name        = "streak",
        description = "🔥 Listening streak — consecutive active days tracker",
    )
    @app_commands.describe(user="Target user (default: server-wide)")
    async def analytics_streak(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer()

        uid  = user.id if user else None
        data = await self.bot.db.get_analytics_streak(interaction.guild_id, uid)

        current = data["current_streak"]
        longest = data["longest_streak"]
        total   = data["total_days"]
        dates   = data["active_dates"]

        def streak_icon(n: int) -> str:
            if n >= 30: return "🔥🔥🔥"
            if n >= 15: return "🔥🔥"
            if n >= 7:  return "🔥"
            if n >= 3:  return "✨"
            if n >= 1:  return "⚡"
            return "💤"

        # 5-week calendar grid
        today      = date.today()
        active_set = set(date.fromisoformat(d) for d in dates)
        # Start from Monday 4 weeks ago
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=4)

        calendar_lines: list[str] = ["`  Mo Tu We Th Fr Sa Su`"]
        for week in range(5):
            row        = ""
            week_start = start + timedelta(weeks=week)
            for dow in range(7):
                day = week_start + timedelta(days=dow)
                if day > today:
                    row += "   "
                elif day in active_set:
                    row += " ✅"
                else:
                    row += " ◻️"
            week_label = week_start.strftime("%m/%d")
            calendar_lines.append(f"`{week_label}`{row}")

        milestones: list[str] = []
        for days_ms, label in [(7, "7-Day 🔥"), (15, "15-Day 🔥🔥"), (30, "30-Day 🔥🔥🔥")]:
            check = "✅" if longest >= days_ms else "⬜"
            milestones.append(f"{check} {label} Streak")

        title_suffix = f" — {user.display_name}" if user else ""
        embed = discord.Embed(
            title       = f"🔥  Listening Streak{title_suffix}",
            description = (
                f"{streak_icon(current)} **Current Streak: {current} day{'s' if current != 1 else ''}**\n"
                f"🏆 **Longest Streak: {longest} day{'s' if longest != 1 else ''}**\n"
                f"📅 **Total Active Days: {total}**\n\n"
                "**📆 Last 5 Weeks  (✅ = active)**\n"
                + "\n".join(calendar_lines)
                + "\n\n**🏅 Milestones**\n"
                + "\n".join(milestones)
            ),
            color = 0xFF7F50,
        )
        if user:
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"📊 Tier Analytics · {interaction.guild.name}")
        await interaction.followup.send(embed=embed)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(AnalyticsCog(bot))
