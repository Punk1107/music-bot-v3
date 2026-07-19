# -*- coding: utf-8 -*-
"""
utils/embeds.py — Discord embed factories for Music Bot V3.

V3 additions:
  - now_playing_embed: includes Unicode progress bar + elapsed/remaining time
  - favorites_embed: list user's saved favorites
  - dj_status_embed: shows active DJ role for a guild
  - auto_playlist_embed: notification when auto-playlist kicks in
  - All embeds use dynamic accent color from track thumbnail
"""

from __future__ import annotations

import math
from typing import Optional, TYPE_CHECKING

import discord

from utils.formatters import (
    format_duration, make_progress_bar, truncate, number_emoji
)

if TYPE_CHECKING:
    from core.player import GuildPlayer
    from models.track import Track


# ── Basic embeds ──────────────────────────────────────────────────────────────

def error_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"❌ {title}",
        description = description,
        color       = 0xE53E3E,
    )


def success_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"✅ {title}",
        description = description,
        color       = 0x48BB78,
    )


def info_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"ℹ️ {title}",
        description = description,
        color       = 0x63B3ED,
    )


def warning_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"⚠️ {title}",
        description = description,
        color       = 0xF6E05E,
    )


# ── Track-added embed ─────────────────────────────────────────────────────────

def track_added_embed(
    track:      "Track",
    position:   int,
    color:      int = 0x5865F2,
    requester:  Optional[discord.User] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title       = "🎵 Added to Queue",
        description = f"[{truncate(track.title, 80)}]({track.url})",
        color       = color,
    )
    embed.add_field(name="⏱ Duration", value=track.duration_str, inline=True)
    embed.add_field(name="📋 Position", value=f"#{position}", inline=True)
    if track.uploader:
        embed.add_field(name="👤 Uploader", value=truncate(track.uploader, 40), inline=True)
    if requester:
        embed.set_footer(text=f"Requested by {requester.display_name}", icon_url=requester.display_avatar.url)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


def playlist_added_embed(count: int, color: int = 0x5865F2) -> discord.Embed:
    return discord.Embed(
        title       = "📋 Playlist Added",
        description = f"**{count}** tracks added to the queue.",
        color       = color,
    )


# ── Now Playing (V3: progress bar + timer) ────────────────────────────────────

def now_playing_embed(
    player:   "GuildPlayer",
    color:    int = 0x5865F2,
    bot_user: Optional[discord.ClientUser] = None,
) -> discord.Embed:
    """
    Full now-playing embed with:
      - Track title (linked)
      - Unicode progress bar
      - Elapsed / total time
      - Loop mode, volume, active effects
      - Requester attribution
      - DJ mode indicator if active
    """
    track = player.now_playing
    if not track:
        return info_embed("Nothing Playing", "The queue is empty.")

    fraction  = player.progress_fraction()
    elapsed   = format_duration(player.elapsed_seconds)
    total     = format_duration(track.duration) if track.duration else "?"
    bar       = make_progress_bar(fraction, width=20)

    # Progress line
    progress_line = f"`{elapsed}` {bar} `{total}`"
    if track.duration:
        pct = int(fraction * 100)
        progress_line += f"  **{pct}%**"

    description = (
        f"[**{truncate(track.title, 80)}**]({track.url})\n"
        f"👤 {truncate(track.uploader or 'Unknown', 40)}\n\n"
        f"▶ **Progress**\n{progress_line}"
    )

    embed = discord.Embed(description=description, color=color)

    # Loop + volume row
    loop_label = player.loop_mode.label()
    vol_label  = f"🔊 {int(player.volume * 100)}%"
    embed.add_field(name=loop_label, value=vol_label, inline=True)

    # Queue count completes the compact playback status row (as in V2).
    q_size = len(player)
    embed.add_field(
        name="📋 Queue",
        value=f"`{q_size} track{'s' if q_size != 1 else ''}`",
        inline=True,
    )

    # Effects row
    if player.effects:
        eff_str = " · ".join(e.display_name() for e in player.effects[:6])
        embed.add_field(name="🎛 Effects", value=eff_str, inline=True)

    # Queue size
    q_size = len(player)
    if False and q_size:  # Queue is always shown in the compact status row above.
        embed.add_field(name="📋 Up Next", value=f"{q_size} track{'s' if q_size != 1 else ''}", inline=True)

    # Requester
    footer_parts = []
    if track.requested_by_name:
        footer_parts.append(f"Requested by {track.requested_by_name}")
    if track.is_favorite:
        footer_parts.append("❤️ Favorite")
    if footer_parts:
        embed.set_footer(text=" · ".join(footer_parts))

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    embed.set_author(name="▶ Now Playing", icon_url=bot_user.display_avatar.url if bot_user else None)
    return embed


# ── Search results ────────────────────────────────────────────────────────────

def search_results_embed(
    query:   str,
    tracks:  list["Track"],
    color:   int = 0x5865F2,
) -> discord.Embed:
    embed = discord.Embed(
        title       = f"🔍 Search: {truncate(query, 50)}",
        description = "Select a track from the dropdown below:",
        color       = color,
    )
    for i, track in enumerate(tracks[:10], 1):
        embed.add_field(
            name  = f"{number_emoji(i)} {truncate(track.title, 60)}",
            value = f"⏱ {track.duration_str} · 👤 {truncate(track.uploader or '?', 30)}",
            inline= False,
        )
    return embed


# ── Queue embed ───────────────────────────────────────────────────────────────

def queue_embed(
    player:  "GuildPlayer",
    page:    int = 1,
    per_page: int = 10,
    color:   int = 0x5865F2,
) -> discord.Embed:
    queue = player.queue
    total = len(queue)
    total_pages = max(1, math.ceil(total / per_page))
    page  = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end   = start + per_page
    items = queue[start:end]

    # Total duration
    total_dur = sum(t.duration for t in queue if t.duration)
    total_dur_str = format_duration(total_dur) if total_dur else "?"

    embed = discord.Embed(
        title = f"📋 Queue — {total} track{'s' if total != 1 else ''} · {total_dur_str}",
        color = color,
    )

    if player.now_playing:
        embed.add_field(
            name  = "▶ Now Playing",
            value = f"[{truncate(player.now_playing.title, 60)}]({player.now_playing.url})",
            inline= False,
        )

    if items:
        lines = []
        for i, track in enumerate(items, start + 1):
            req = f" — {track.requested_by_name}" if track.requested_by_name else ""
            fav = " ❤️" if track.is_favorite else ""
            lines.append(f"`{i}.` [{truncate(track.title, 55)}]({track.url}) `{track.duration_str}`{req}{fav}")
        embed.description = "\n".join(lines)
    else:
        embed.description = "*Queue is empty.*"

    embed.set_footer(text=f"Page {page}/{total_pages} · Loop: {player.loop_mode.value.capitalize()} · Vol: {int(player.volume * 100)}%")
    return embed


# ── Favorites embeds (V3 NEW) ─────────────────────────────────────────────────

def favorites_list_embed(
    favorites: list[dict],
    user:      discord.User,
    page:      int = 1,
    per_page:  int = 10,
    color:     int = 0xFF69B4,
) -> discord.Embed:
    total       = len(favorites)
    total_pages = max(1, math.ceil(total / per_page))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page
    items       = favorites[start:start + per_page]

    embed = discord.Embed(
        title = f"❤️ {user.display_name}'s Favorites ({total})",
        color = color,
    )
    if items:
        lines = []
        for i, fav in enumerate(items, start + 1):
            track = fav["track"]
            lines.append(
                f"`{i}.` **{truncate(fav['name'], 40)}** — [{truncate(track.title, 50)}]({track.url}) `{track.duration_str}`"
            )
        embed.description = "\n".join(lines)
    else:
        embed.description = "*No favorites yet. Use `/favorite add` to save a track!*"

    embed.set_footer(text=f"Page {page}/{total_pages}")
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def favorite_added_embed(name: str, track: "Track", color: int = 0xFF69B4) -> discord.Embed:
    embed = discord.Embed(
        title       = "❤️ Added to Favorites",
        description = f"Saved **[{truncate(track.title, 60)}]({track.url})** as `{name}`",
        color       = color,
    )
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


def favorite_removed_embed(name: str) -> discord.Embed:
    return success_embed("Removed from Favorites", f"Deleted favorite `{name}`.")


# ── DJ mode embeds (V3 NEW) ───────────────────────────────────────────────────

def dj_set_embed(role: discord.Role) -> discord.Embed:
    return discord.Embed(
        title       = "🎚️ DJ Role Set",
        description = (
            f"Only users with the {role.mention} role can now use control commands.\n"
            f"*เฉพาะผู้มี role {role.mention} เท่านั้นที่ใช้คำสั่งควบคุมได้*"
        ),
        color       = role.color.value or 0x5865F2,
    )


def dj_cleared_embed() -> discord.Embed:
    return success_embed("DJ Role Cleared", "All users can now control the bot.")


# ── Request channel embeds (V3 NEW) ───────────────────────────────────────────

def request_channel_set_embed(channel: discord.TextChannel) -> discord.Embed:
    return discord.Embed(
        title       = "📻 Request Channel Set",
        description = (
            f"Users can now type song names or URLs directly in {channel.mention}.\n"
            f"*ผู้ใช้สามารถพิมพ์ชื่อเพลงหรือ URL โดยตรงใน {channel.mention}*"
        ),
        color       = 0x5865F2,
    )


# ── Auto-playlist embed (V3 NEW) ──────────────────────────────────────────────

def auto_playlist_embed(track_count: int) -> discord.Embed:
    return discord.Embed(
        title       = "🎼 Auto-Playlist",
        description = (
            f"Queue was empty. Added **{track_count}** tracks from your recent history.\n"
            f"*คิวหมดแล้ว เพิ่ม {track_count} เพลงจากประวัติการฟังล่าสุด*"
        ),
        color       = 0x9B59B6,
    )


# ── Bot stats embed ───────────────────────────────────────────────────────────

def stats_embed(
    guild_id:     int,
    user_stats:   Optional[dict],
    user:         discord.User,
    history_rows: list[dict],
    color:        int = 0x5865F2,
) -> discord.Embed:
    embed = discord.Embed(
        title = f"📊 Stats for {user.display_name}",
        color = color,
    )
    if user_stats:
        t   = user_stats.get("total_tracks_requested", 0)
        lt  = format_duration(user_stats.get("total_listening_time", 0))
        embed.add_field(name="🎵 Tracks Requested", value=str(t), inline=True)
        embed.add_field(name="⏱ Listening Time",   value=lt,     inline=True)
    else:
        embed.description = "*No listening data yet. Start playing some music!*"

    if history_rows:
        lines = []
        for row in history_rows[:5]:
            try:
                from models.track import Track as T
                t = T.from_json(row["track_data"])
                lines.append(f"• [{truncate(t.title, 50)}]({t.url})")
            except Exception:
                pass
        if lines:
            embed.add_field(name="🕐 Recent History", value="\n".join(lines), inline=False)

    embed.set_thumbnail(url=user.display_avatar.url)
    return embed
