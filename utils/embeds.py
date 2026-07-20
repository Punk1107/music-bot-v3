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
    format_duration, format_views, make_progress_bar,
    make_knob_progress_bar, truncate, number_emoji
)

if TYPE_CHECKING:
    from core.player import GuildPlayer
    from models.track import Track


# ── Basic embeds ──────────────────────────────────────────────────────────────

def error_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"✖  {title}",
        description = description,
        color       = 0xFF4757,
    )


def success_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"✔  {title}",
        description = description,
        color       = 0x2ED573,
    )


def info_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"ℹ  {title}",
        description = description,
        color       = 0x70A1FF,
    )


def warning_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title       = f"⚠  {title}",
        description = description,
        color       = 0xFFD32A,
    )


# ── Track-added embed ─────────────────────────────────────────────────────────

def track_added_embed(
    track:      "Track",
    position:   int,
    color:      int = 0x5865F2,
    requester:  Optional[discord.User] = None,
) -> discord.Embed:
    """
    Track-added card with 3-column inline fields matching the screenshot layout:

      🎵 Added to Queue
      **[Title](url)**

      ⏱ Duration  |  📋 Position  |  👤 Uploader
      3:31              #1            marr team official

      Footer: avatar · Requested by …
    """
    embed = discord.Embed(
        title       = "🎵  Added to Queue",
        description = f"{truncate(track.title, 80)}",
        color       = color,
    )

    embed.add_field(name="⏱ Duration",  value=f"{track.duration_str}",                  inline=True)
    embed.add_field(name="📋 Position", value=f"#{position}",                            inline=True)
    embed.add_field(name="👤 Uploader", value=truncate(track.uploader or "Unknown", 35), inline=True)

    if requester:
        embed.set_footer(
            text     = f"Requested by {requester.display_name}",
            icon_url = requester.display_avatar.url,
        )
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


def playlist_added_embed(count: int, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(
        description = f"**{count}** tracks have been added to the queue.",
        color       = color,
    )
    embed.set_author(name="📋  PLAYLIST ADDED")
    return embed


# ── Now Playing (V3: progress bar + timer) ────────────────────────────────────

def now_playing_embed(
    player:   "GuildPlayer",
    color:    int = 0x5865F2,
    bot_user: Optional[discord.ClientUser] = None,
    paused:   bool = False,
) -> discord.Embed:
    """
    Gen-2 style now-playing embed with V3 data:

      Title   : 🎵 Track Title  (embed.url makes it clickable)
      Desc    : 🔵 Uploader

      Fields  : ⏱ Duration  |  👁 Views    |  📋 In Queue
                👤 Requested by  |  🔁 Loop  |  🔊 Volume
                ▶️ Progress  ← full-width knob progress bar

      Footer  : bot avatar · Music Bot V3 • Now Playing
      Thumbnail: track art
    """
    track = player.now_playing
    if not track:
        return info_embed("Nothing Playing", "The queue is empty.")

    # ── Progress bar ─────────────────────────────────────────────────────────
    fraction = player.progress_fraction()
    elapsed  = format_duration(player.elapsed_seconds)
    total    = format_duration(track.duration) if track.duration else "?"
    bar_line = make_knob_progress_bar(fraction, elapsed, total, paused=paused, url=track.url or "")

    # ── Main embed ────────────────────────────────────────────────────────────
    embed = discord.Embed(
        title       = f"🎵  {truncate(track.title, 80)}",
        url         = track.url or None,
        description = f"🔵  {truncate(track.uploader or 'Unknown', 40)}",
        color       = color,
    )

    # ── Row 1: Duration | Views | In Queue ───────────────────────────────────
    dur_val  = format_duration(track.duration) if track.duration else "?"
    view_val = format_views(track.view_count)  if track.view_count else "—"
    q_size   = len(player)
    q_val    = f"{q_size} track{'s' if q_size != 1 else ''}"

    embed.add_field(name="⏱ Duration",  value=dur_val,  inline=True)
    embed.add_field(name="👁 Views",    value=view_val, inline=True)
    embed.add_field(name="📋 In Queue", value=q_val,    inline=True)

    # ── Row 2: Requested by | Loop | Volume ──────────────────────────────────
    req_val  = f"@{track.requested_by_name}" if track.requested_by_name else "—"
    loop_val = player.loop_mode.value.capitalize()
    vol_val  = f"{int(player.volume * 100)}%"

    embed.add_field(name="👤 Requested by", value=req_val,  inline=True)
    embed.add_field(name="🔁 Loop",         value=loop_val, inline=True)
    embed.add_field(name="🔊 Volume",       value=vol_val,  inline=True)

    # ── Progress bar (full width) ─────────────────────────────────────────────
    embed.add_field(name="▶️ Progress", value=bar_line, inline=False)

    # Optional effects
    if player.effects:
        eff_str = " · ".join(e.display_name() for e in player.effects[:4])
        embed.add_field(name="🎛 Effects", value=eff_str, inline=False)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_icon = bot_user.display_avatar.url if bot_user else None
    embed.set_footer(
        text     = "Music Bot V3  •  Now Playing",
        icon_url = footer_icon,
    )

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    return embed



# ── Search results ────────────────────────────────────────────────────────────

def search_results_embed(
    query:   str,
    tracks:  list["Track"],
    color:   int = 0x5865F2,
) -> discord.Embed:
    lines = []
    for i, track in enumerate(tracks[:10], 1):
        lines.append(
            f"{number_emoji(i)}  **{truncate(track.title, 55)}**\n"
            f"    ↳ `{track.duration_str}`  ·  {truncate(track.uploader or '?', 30)}"
        )
    embed = discord.Embed(
        description = (
            f"*{truncate(query, 60)}*\n\n"
            + "\n\n".join(lines)
        ),
        color       = color,
    )
    embed.set_author(name="🔍  SEARCH RESULTS")
    return embed


# ── Queue embed ───────────────────────────────────────────────────────────────

def queue_embed(
    player:   "GuildPlayer",
    page:     int = 1,
    per_page: int = 10,
    color:    int = 0x5865F2,
) -> discord.Embed:
    queue       = player.queue
    total       = len(queue)
    total_pages = max(1, math.ceil(total / per_page))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page
    items       = queue[start:start + per_page]

    total_dur     = sum(t.duration for t in queue if t.duration)
    total_dur_str = format_duration(total_dur) if total_dur else "?"

    _div  = "─" * 32
    lines = []

    # ── Now-playing banner ────────────────────────────────────────────────────
    if player.now_playing:
        elapsed  = format_duration(player.elapsed_seconds)
        total_t  = format_duration(player.now_playing.duration)
        mini_bar = make_progress_bar(player.progress_fraction(), player.now_playing.url, width=14)
        lines.append(
            f"**▶  Now Playing**\n"
            f"[{truncate(player.now_playing.title, 55)}]({player.now_playing.url})\n"
            f"`{elapsed}` {mini_bar} `{total_t}`\n"
            f"{_div}"
        )

    # ── Queue items ───────────────────────────────────────────────────────────
    if items:
        for i, track in enumerate(items, start + 1):
            req = f" · *{track.requested_by_name}*" if track.requested_by_name else ""
            fav = " ❤️" if track.is_favorite else ""
            lines.append(
                f"`{i:>2}.` [{truncate(track.title, 50)}]({track.url})  `{track.duration_str}`{req}{fav}"
            )
    else:
        lines.append("*Queue is empty.*")

    embed = discord.Embed(
        title       = f"📋  Queue  —  {total} track{'s' if total != 1 else ''}  ·  {total_dur_str}",
        description = "\n".join(lines),
        color       = color,
    )
    embed.set_footer(
        text=f"Page {page}/{total_pages}  ·  🔁 {player.loop_mode.value.capitalize()}  ·  🔊 {int(player.volume * 100)}%"
    )
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
