# -*- coding: utf-8 -*-
"""cogs/info.py — Info commands: /history, /stats, /help for Music Bot V3."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import info_embed, stats_embed
from utils.formatters import format_uptime

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class InfoCog(commands.Cog, name="Info"):
    """Informational commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    @app_commands.command(name="history", description="Show recent play history")
    @app_commands.describe(user="Target user (default: yourself)")
    async def history(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        rows   = await self.bot.db.get_history(
            interaction.guild_id, limit=10, user_id=target.id
        )
        if not rows:
            await interaction.followup.send(
                embed=info_embed("No History", f"No play history for {target.display_name}."),
                ephemeral=True,
            )
            return

        from models.track import Track
        from utils.formatters import truncate
        lines = []
        for i, row in enumerate(rows, 1):
            try:
                t = Track.from_json(row["track_data"])
                ts = row.get("played_at", "")[:10]
                skip_icon = "⏭" if row.get("skipped") else "✅"
                lines.append(f"`{i}.` {skip_icon} [{truncate(t.title, 55)}]({t.url}) `{ts}`")
            except Exception:
                pass

        embed = discord.Embed(
            title       = f"🕐 Play History — {target.display_name}",
            description = "\n".join(lines),
            color       = 0x5865F2,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="stats", description="Show listening stats for a user")
    @app_commands.describe(user="Target user (default: yourself)")
    async def stats(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        user_stats = await self.bot.db.get_user_stats(interaction.guild_id, target.id)
        history    = await self.bot.db.get_history(interaction.guild_id, limit=5, user_id=target.id)
        embed      = stats_embed(interaction.guild_id, user_stats, target, history)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="botstats", description="Show bot performance metrics")
    async def botstats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        active_players = sum(
            1 for p in self.bot._players.values() if p.now_playing is not None
        )
        guild_count = len(self.bot.guilds)
        uptime_str  = format_uptime(self.bot.start_time)

        from core.circuit_breaker import BreakerState
        yt_state = self.bot.yt_breaker.state.value
        sp_state = self.bot.sp_breaker.state.value

        embed = discord.Embed(title="📊 Bot Statistics", color=0x5865F2)
        embed.add_field(name="🌐 Guilds",         value=str(guild_count),   inline=True)
        embed.add_field(name="🎵 Active Players", value=str(active_players), inline=True)
        embed.add_field(name="⏱ Uptime",         value=uptime_str,         inline=True)
        embed.add_field(name="⚡ YT Circuit",     value=yt_state,           inline=True)
        embed.add_field(name="⚡ Spotify Circuit",value=sp_state,           inline=True)

        import psutil
        try:
            process = psutil.Process()
            mem_mb  = process.memory_info().rss / 1024 / 1024
            embed.add_field(name="💾 Memory", value=f"{mem_mb:.1f} MB", inline=True)
        except Exception:
            pass

        embed.set_footer(text=f"discord.py {discord.__version__} · Music Bot V3")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="help", description="Show all available commands")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title       = "🎵 Music Bot V3 — Commands",
            description = "All slash commands for Music Bot V3.",
            color       = 0x5865F2,
        )
        sections = {
            "🎵 Playback": [
                "`/join`   — Join your voice channel",
                "`/leave`  — Disconnect and clear queue",
                "`/play <query>` — YouTube/Spotify URL or search",
                "`/search <query>` — Search and choose from results",
                "`/pause` / `/resume` — Pause/resume playback",
                "`/skip`  — Skip current track",
                "`/stop`  — Stop playback and clear queue (bot stays in channel)",
                "`/nowplaying` — Show now-playing with progress bar",
            ],
            "📋 Queue": [
                "`/queue [page]` — Paginated queue view",
                "`/shuffle` — Shuffle queue",
                "`/clear`  — Clear entire queue",
                "`/loop`   — Cycle loop mode (Off→Track→Queue)",
                "`/remove <pos>` — Remove track at position",
                "`/move <from> <to>` — Reorder track",
            ],
            "🎛 Audio": [
                "`/volume <0-200>` — Set playback volume",
                "`/effects <name>` — Toggle an audio effect",
                "`/effects_list`   — Show all 18 effects",
                "`/effects_clear`  — Disable all effects",
                "`/quality <preset>` — Set audio quality",
            ],
            "❤️ Favorites (V3)": [
                "`/favorite add [name]`   — Save current track",
                "`/favorite list`         — View your favorites",
                "`/favorite play <name>`  — Play a favorite",
                "`/favorite remove <name>` — Delete a favorite",
            ],
            "⚙️ Admin (V3)": [
                "`/djset role @role`       — Set DJ role",
                "`/djset clear`            — Remove DJ restriction",
                "`/requestchannel set #ch` — Set request channel",
                "`/requestchannel clear`   — Remove request channel",
                "`/autoplaylist on/off`    — Toggle auto-playlist",
            ],
            "📊 Info": [
                "`/history [user]` — Play history",
                "`/stats [user]`   — Listening stats",
                "`/botstats`       — Bot performance metrics",
                "`/help`           — This help message",
            ],
        }
        for section, cmds in sections.items():
            embed.add_field(name=section, value="\n".join(cmds), inline=False)

        embed.set_footer(text="V3 • FFmpeg only • No Lavalink • EN/TH support")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(InfoCog(bot))
