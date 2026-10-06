# -*- coding: utf-8 -*-
"""cogs/info.py — Info commands: /history, /stats, /help for Music Bot V3."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

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

        embed.set_footer(text=f"discord.py {discord.__version__} · Music Bot Gen 4.0")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="help",
        description="Comprehensive guide to all Music Bot Gen 4.0 slash commands",
    )
    @app_commands.describe(
        category="Filter by command domain (leave blank to view full directory)",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="🌐 All Domains (Comprehensive Directory)", value="all"),
        app_commands.Choice(name="🎵 1. Core Playback",                     value="playback"),
        app_commands.Choice(name="🗳️ 2. Democratic Voice Voting",           value="voting"),
        app_commands.Choice(name="📋 3. Queue Operations & Control",         value="queue"),
        app_commands.Choice(name="⏩ 4. Seeking & Looping",                  value="seeking"),
        app_commands.Choice(name="📑 5. Chapters, Lyrics & Recommendations", value="chapters_lyrics"),
        app_commands.Choice(name="🔊 6. Multi-Source Search",                value="search"),
        app_commands.Choice(name="🎚 7. Audio DSP, Equalizer & Soundstage",  value="dsp"),
        app_commands.Choice(name="🎨 8. Customization, Favorites & Presets", value="customization"),
        app_commands.Choice(name="⚙️ 9. Administration & Diagnostics",       value="admin"),
    ])
    async def help_cmd(
        self,
        interaction: discord.Interaction,
        category: str = "all",
    ) -> None:
        await interaction.response.defer(ephemeral=False)
        embed = build_help_embed(category)
        view  = HelpNavigationSelectView(current_category=category)
        msg   = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        view.message = msg


# ── Help Catalogue & UI System ───────────────────────────────────────────────

HELP_CATEGORIES: dict[str, dict[str, Any]] = {
    "playback": {
        "title": "🎵 Core Playback",
        "emoji": "🎵",
        "description": "Essential audio streaming, playback controls, and voice channel management.",
        "commands": [
            ("`/play <query> [shuffle]`", "Play from YouTube, Spotify, SoundCloud, Bandcamp, or search"),
            ("`/playnext <query>`", "Enqueue track directly after currently playing song"),
            ("`/search <query>`", "Search YouTube and select via interactive dropdown"),
            ("`/pause`", "Pause audio playback"),
            ("`/resume`", "Resume paused playback"),
            ("`/skip`", "Skip current track (vote for users; instant for DJ/Admin)"),
            ("`/stop`", "Halt playback and clear queue (bot stays in voice channel)"),
            ("`/nowplaying`", "Show Gen-2 embed with live two-tone progress bar"),
            ("`/join`", "Connect bot to your current voice channel"),
            ("`/leave`", "Disconnect bot from voice channel and clear queue"),
        ],
    },
    "voting": {
        "title": "🗳️ Democratic Voice Voting",
        "emoji": "🗳️",
        "description": "Quorum-based democratic voting for listeners (ceil(50% of listeners)).",
        "commands": [
            ("`/voteskip`", "Start a democratic vote to skip current track"),
            ("`/voteshuffle`", "Start a democratic vote to shuffle the server queue"),
            ("`/voteclear`", "Start a democratic vote to clear all songs from queue"),
        ],
    },
    "queue": {
        "title": "📋 Queue Operations & Control",
        "emoji": "📋",
        "description": "Manage, organize, reorder, lock, import, and export the playback queue.",
        "commands": [
            ("`/queue [page]`", "View paginated server queue with total duration"),
            ("`/shuffle`", "Randomize the order of tracks in the queue"),
            ("`/clear`", "Clear all tracks from the queue (DJ/Admin)"),
            ("`/loop`", "Cycle loop mode: Off ➔ Track ➔ Queue"),
            ("`/remove <pos>`", "Remove track by its 1-based queue position"),
            ("`/move <from> <to>`", "Atomically reposition a track within the queue"),
            ("`/jump <pos>`", "Jump directly to track position in queue"),
            ("`/undo`", "Undo last queue action (shuffle, clear, remove, move)"),
            ("`/qsearch <query>`", "Search for a specific song inside active queue"),
            ("`/qhistory [limit]`", "View recently played tracks with replay buttons"),
            ("`/queuelock <locked>`", "Lock queue to prevent non-DJ/Admins from adding tracks"),
            ("`/queueperm <level>`", "Configure who can add tracks (all, dj, admin)"),
            ("`/duplicates <mode>`", "Configure duplicate track handling (allow, warn, block)"),
            ("`/qexport [fmt]`", "Export current queue to file (json, csv, txt)"),
            ("`/qimport [mode]`", "Import a previously saved queue file"),
        ],
    },
    "seeking": {
        "title": "⏩ Seeking & Looping",
        "emoji": "⏩",
        "description": "Hot-reload audio seeking, forward/rewind, and precision Loop A-B repetition.",
        "commands": [
            ("`/seek <time>`", "Hot-reload seek to timestamp (e.g. 1:30, 90, 1m20s)"),
            ("`/forward [secs]`", "Fast-forward playback by seconds (default: 15s)"),
            ("`/rewind [secs]`", "Rewind playback by seconds (default: 15s)"),
            ("`/replay` / `/restart`", "Restart currently playing track from the beginning"),
            ("`/loopab <start> [end]`", "Repeat segment between timestamps (e.g. /loopab 0:30 1:15)"),
            ("`/loopab_off`", "Turn off active Loop A-B segment repetition"),
        ],
    },
    "chapters_lyrics": {
        "title": "📑 Chapters, Lyrics & Recommendations",
        "emoji": "📑",
        "description": "Deep media inspection, synchronized lyrics, and smart automated playback.",
        "commands": [
            ("`/chapters`", "Browse and jump to video chapters via dropdown menu"),
            ("`/chapter_jump <time>`", "Jump directly to video chapter timestamp (/cjump)"),
            ("`/lyrics [sync]`", "Display synchronized lyrics with live tracking & paginator"),
            ("`/autoplay <mode>`", "Toggle Smart Autoplay YouTube Radio Mix (on, off, status)"),
        ],
    },
    "search": {
        "title": "🔊 Multi-Source Search",
        "emoji": "🔊",
        "description": "Direct multi-platform search without leaving Discord.",
        "commands": [
            ("`/scsearch <query>`", "Search SoundCloud and select tracks via interactive dropdown"),
        ],
    },
    "dsp": {
        "title": "🎚 Audio DSP, Equalizer & Soundstage",
        "emoji": "🎚",
        "description": "Studio DSP filtergraph pipeline, 4-band equalizer, and soundstage enhancement.",
        "commands": [
            ("`/volume <0-200>`", "Adjust playback volume percentage"),
            ("`/effects <name>`", "Toggle 1 of 18 audio effects (bass, nightcore, 8D, etc.)"),
            ("`/effects_list`", "Show all 18 DSP audio effects with active/inactive status"),
            ("`/effects_clear`", "Disable all active audio effects simultaneously"),
            ("`/quality <preset>`", "Set audio quality preset (low, med, high, ultra)"),
            ("`/equalizer` / `/eq`", "4-Band Equalizer: preset, custom, view, reset"),
            ("`/loudnorm [bool]`", "Toggle EBU R128 broadcast loudness normalization"),
            ("`/pan <balance>`", "Adjust audio balance from Left (-1.0) to Right (+1.0)"),
            ("`/stereowide <width>`", "Set stereo soundstage width: Mono (0.0x) to Ultra (2.0x)"),
            ("`/speed <rate>`", "Set playback speed (0.75x–2.0x) without pitch changes"),
            ("`/pitch <semitones>`", "Shift pitch (-2 to +2 st) without tempo changes"),
            ("`/crossfade <secs>`", "Set track crossfade duration (0s, 3s, 5s, 8s)"),
            ("`/silencetrim`", "Toggle automatic silence trimming from intro/outro"),
            ("`/replaygain`", "Toggle dynamic audio loudness normalization (dynaudnorm)"),
            ("`/playbackinfo`", "Display all active audio enhancement and DSP settings"),
        ],
    },
    "customization": {
        "title": "🎨 Customization, Favorites & Presets",
        "emoji": "🎨",
        "description": "Personal favorites, guild audio bundles, embed themes, and auto-sleep timers.",
        "commands": [
            ("`/favorite <action>`", "Personal favorites: add, list, play, remove"),
            ("`/bookmark <action>`", "Queue snapshots: save, load, list, delete"),
            ("`/preset <action>`", "Guild audio presets: load, save, list, delete"),
            ("`/theme <style>`", "Set server embed theme: classic, spotify, minimal, glass"),
            ("`/themeinfo`", "Preview all 4 visual embed themes"),
            ("`/sleep <duration>`", "Set auto-disconnect timer (e.g. 20m, 1h, off)"),
            ("`/sleepstatus`", "View remaining time on active sleep timer"),
            ("`/language <locale>`", "Switch server UI language across 9 supported languages"),
            ("`/languageinfo`", "Display current server language and list all supported locales"),
        ],
    },
    "admin": {
        "title": "⚙️ Administration & Diagnostics",
        "emoji": "⚙️",
        "description": "Server governance, offline web dashboard, metrics, and health diagnostics.",
        "commands": [
            ("`/djset <role|clear>`", "Configure DJ role restriction for music commands"),
            ("`/requestchannel <set|clear>`", "Designate dedicated text channel for NLU requests"),
            ("`/autoplaylist <on|off>`", "Toggle auto-enqueue from history when queue empties"),
            ("`/idletimeout <secs>`", "Configure idle auto-disconnect timeout (60-3600 seconds)"),
            ("`/history [user]`", "Show recent play history for server or member"),
            ("`/stats [user]`", "Show listening statistics and track request counts"),
            ("`/botstats`", "Display bot metrics: guilds, active players, memory, circuits"),
            ("`/health`", "Full diagnostic report: caches, memory, self-test status"),
            ("`/cacheinfo`", "Detailed LRU cache hit rates, memory, and evictions"),
            ("`/dashboard`", "Local offline web dashboard URL (http://localhost:8080)"),
            ("`/analytics <cmd>`", "Tier analytics: heatmap, genre, peak, top, streak"),
            ("`/help [category]`", "Interactive command guide and documentation"),
        ],
    },
}

_CAT_KEYS = list(HELP_CATEGORIES.keys())


def build_help_embed(category_key: str = "all") -> discord.Embed:
    total_cmds = sum(len(cat["commands"]) for cat in HELP_CATEGORIES.values())

    if category_key == "all" or category_key not in HELP_CATEGORIES:
        embed = discord.Embed(
            title="🎵 Music Bot Gen 4.0 — Comprehensive Command Guide",
            description=(
                f"Music Bot Gen 4.0 provides **{total_cmds} slash commands** across **9 specialized domains**.\n"
                "Use the **select dropdown** or buttons below to explore any category in detail."
            ),
            color=0x5865F2,
        )
        for key, cat in HELP_CATEGORIES.items():
            title = f"{cat['title']} ({len(cat['commands'])})"
            lines = [f"{c[0]} — {c[1]}" for c in cat["commands"]]
            full_text = "\n".join(lines)
            if len(full_text) <= 1024:
                embed.add_field(name=title, value=full_text, inline=False)
            else:
                half = (len(lines) + 1) // 2
                embed.add_field(name=f"{title} (Part 1)", value="\n".join(lines[:half]), inline=False)
                embed.add_field(name=f"{title} (Part 2)", value="\n".join(lines[half:]), inline=False)

        embed.set_footer(
            text=f"Gen 4.0 • Zero Lavalink • Studio DSP • 9-Language Parity • {total_cmds} Commands"
        )
        return embed

    cat = HELP_CATEGORIES[category_key]
    idx = _CAT_KEYS.index(category_key) + 1

    lines = [f"• **{c[0]}**\n  └ {c[1]}" for c in cat["commands"]]
    description = f"{cat['description']}\n\n" + "\n\n".join(lines)

    embed = discord.Embed(
        title=f"{cat['title']} — Commands ({len(cat['commands'])})",
        description=description,
        color=0x5865F2,
    )
    embed.set_footer(
        text=f"Gen 4.0 • Domain {idx} of {len(_CAT_KEYS)} • {total_cmds} Total Commands"
    )
    return embed


class HelpCategorySelect(discord.ui.Select):
    """Interactive category dropdown for /help."""

    def __init__(self, current_category: str = "all") -> None:
        options = [
            discord.SelectOption(
                label="All Domains (Full Directory)",
                value="all",
                description="Comprehensive directory of all 9 domains",
                emoji="🌐",
                default=(current_category == "all"),
            )
        ]
        for key, cat in HELP_CATEGORIES.items():
            cmd_count = len(cat["commands"])
            options.append(
                discord.SelectOption(
                    label=cat["title"][:100],
                    value=key,
                    description=f"{cmd_count} commands — {cat['description'][:50]}",
                    emoji=cat.get("emoji", "🎵"),
                    default=(current_category == key),
                )
            )
        super().__init__(
            placeholder="Select a domain to explore...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="help_category_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0]
        view: HelpNavigationSelectView = self.view  # type: ignore
        view.current_category = chosen
        view.update_select()
        embed = build_help_embed(chosen)
        await interaction.response.edit_message(embed=embed, view=view)


class HelpNavigationSelectView(discord.ui.View):
    """Interactive view with dropdown menu and pagination buttons for /help."""

    def __init__(self, current_category: str = "all") -> None:
        super().__init__(timeout=180)
        self.current_category = current_category
        self.message: discord.Message | None = None
        self.update_select()

    def update_select(self) -> None:
        # Clear existing select and add fresh one with updated default
        for item in list(self.children):
            if isinstance(item, HelpCategorySelect):
                self.remove_item(item)
        self.add_item(HelpCategorySelect(self.current_category))

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, row=1, custom_id="help_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_category == "all" or self.current_category not in _CAT_KEYS:
            self.current_category = _CAT_KEYS[-1]
        else:
            curr_idx = _CAT_KEYS.index(self.current_category)
            prev_idx = (curr_idx - 1) % len(_CAT_KEYS)
            self.current_category = _CAT_KEYS[prev_idx]

        self.update_select()
        embed = build_help_embed(self.current_category)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1, custom_id="help_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_category == "all" or self.current_category not in _CAT_KEYS:
            self.current_category = _CAT_KEYS[0]
        else:
            curr_idx = _CAT_KEYS.index(self.current_category)
            next_idx = (curr_idx + 1) % len(_CAT_KEYS)
            self.current_category = _CAT_KEYS[next_idx]

        self.update_select()
        embed = build_help_embed(self.current_category)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🌐 Overview", style=discord.ButtonStyle.primary, row=1, custom_id="help_overview")
    async def overview_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current_category = "all"
        self.update_select()
        embed = build_help_embed("all")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(InfoCog(bot))
