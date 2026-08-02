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
    eta_secs:   Optional[int] = None,
) -> discord.Embed:
    """
    Track-added card with 3-column inline fields matching the screenshot layout:

      🎵 Added to Queue
      **[Title](url)**

      ⏱ Duration  |  📋 Position  |  👤 Uploader
      3:31              #1            marr team official
      🕐 Starts in: 5m 20s

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

    if eta_secs is not None and eta_secs > 0:
        embed.add_field(
            name="🕐 Starts in",
            value=format_duration(eta_secs),
            inline=False,
        )

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
    theme:    str  = "classic",
) -> discord.Embed:
    """
    Themed now-playing embed (F27).

    theme options:
      classic — Dynamic colour from thumbnail. Full field layout. (default)
      spotify — Spotify green. Large artwork. Minimal text.
      minimal — Dark background. No fields. Ultra-clean.
      glass   — Frosted pastel. Slightly different typography.
    """
    track = player.now_playing
    if not track:
        return info_embed("Nothing Playing", "The queue is empty.")

    # ── Shared data ───────────────────────────────────────────────────────────
    fraction = player.progress_fraction()
    elapsed  = format_duration(player.elapsed_seconds)
    total    = format_duration(track.duration) if track.duration else "?"
    bar_line = make_knob_progress_bar(fraction, elapsed, total, paused=paused, url=track.url or "", width=32)
    dur_val  = format_duration(track.duration) if track.duration else "?"
    view_val = format_views(track.view_count)  if track.view_count else "—"
    q_size   = len(player)
    q_val    = f"{q_size} track{'s' if q_size != 1 else ''}"
    req_val  = f"@{track.requested_by_name}" if track.requested_by_name else "—"
    loop_val = player.loop_mode.value.capitalize()
    vol_val  = f"{int(player.volume * 100)}%"
    footer_icon = bot_user.display_avatar.url if bot_user else None

    # ── Speed / Pitch indicators (A+ features) ────────────────────────────────
    extras = []
    if hasattr(player, "playback_speed") and abs(player.playback_speed - 1.0) > 0.01:
        extras.append(f"⚡ {player.playback_speed}x")
    if hasattr(player, "pitch_semitones") and player.pitch_semitones != 0:
        sign = "+" if player.pitch_semitones > 0 else ""
        extras.append(f"🎵 {sign}{player.pitch_semitones}st")

    # ── CLASSIC ───────────────────────────────────────────────────────────────
    if theme == "classic":
        embed = discord.Embed(
            title       = f"🎵  {truncate(track.title, 80)}",
            url         = track.url or None,
            description = f"🔵  {truncate(track.uploader or 'Unknown', 40)}",
            color       = color,
        )
        embed.add_field(name="⏱ Duration",      value=dur_val,  inline=True)
        embed.add_field(name="👁 Views",         value=view_val, inline=True)
        embed.add_field(name="📋 In Queue",      value=q_val,    inline=True)
        embed.add_field(name="👤 Requested by",  value=req_val,  inline=True)
        embed.add_field(name="🔁 Loop",          value=loop_val, inline=True)
        embed.add_field(name="🔊 Volume",        value=vol_val,  inline=True)
        embed.add_field(name="▶️ Progress",      value=bar_line, inline=False)
        if player.effects:
            eff_str = " · ".join(e.display_name() for e in player.effects[:4])
            embed.add_field(name="🎛 Effects", value=eff_str, inline=False)
        if extras:
            embed.add_field(name="🎚 Enhancements", value="  ".join(extras), inline=False)
        embed.set_footer(text="Music Bot V3  •  Now Playing", icon_url=footer_icon)
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)

    # ── SPOTIFY ───────────────────────────────────────────────────────────────
    elif theme == "spotify":
        from models.enums import EmbedTheme
        sp_color = EmbedTheme.SPOTIFY.accent_color()
        status   = "⏸ Paused" if paused else "▶ Now Playing"
        embed = discord.Embed(
            title       = truncate(track.title, 80),
            url         = track.url or None,
            description = (
                f"**{truncate(track.uploader or 'Unknown', 40)}**\n\n"
                f"```{bar_line}```\n"
                f"{status}  •  {dur_val}  •  {vol_val} vol"
            ),
            color       = sp_color,
        )
        embed.add_field(name="🔁", value=loop_val, inline=True)
        embed.add_field(name="📋", value=q_val,    inline=True)
        if extras:
            embed.add_field(name="🎚", value="  ".join(extras), inline=True)
        embed.set_footer(text=f"Requested by {req_val}  •  Music Bot V3", icon_url=footer_icon)
        if track.thumbnail:
            embed.set_image(url=track.thumbnail)  # large artwork for Spotify feel

    # ── MINIMAL ───────────────────────────────────────────────────────────────
    elif theme == "minimal":
        from models.enums import EmbedTheme
        mn_color = EmbedTheme.MINIMAL.accent_color()
        status   = "⏸" if paused else "▶"
        extra_str = f"  {' '.join(extras)}" if extras else ""
        embed = discord.Embed(
            description = (
                f"{status}  **{truncate(track.title, 70)}**\n"
                f"*{truncate(track.uploader or 'Unknown', 40)}*\n\n"
                f"`{bar_line}`{extra_str}"
            ),
            color       = mn_color,
        )
        embed.set_footer(text=f"{req_val}  •  {loop_val}  •  {vol_val}", icon_url=footer_icon)

    # ── GLASS ─────────────────────────────────────────────────────────────────
    elif theme == "glass":
        from models.enums import EmbedTheme
        gl_color = EmbedTheme.GLASS.accent_color()
        status   = "⏸ Paused" if paused else "♪  Now Playing"
        embed = discord.Embed(
            title       = f"♪  {truncate(track.title, 75)}",
            url         = track.url or None,
            description = (
                f"*{truncate(track.uploader or 'Unknown', 40)}*\n\n"
                f"```{bar_line}```"
            ),
            color       = gl_color,
        )
        embed.add_field(name="⏱",  value=dur_val,  inline=True)
        embed.add_field(name="🔁", value=loop_val, inline=True)
        embed.add_field(name="🔊", value=vol_val,  inline=True)
        if extras:
            embed.add_field(name="🎚 Enhanced", value="  ".join(extras), inline=False)
        embed.set_footer(
            text     = f"{status}  •  Req: {req_val}",
            icon_url = footer_icon,
        )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)

    else:
        # Fallback to classic
        return now_playing_embed(player, color, bot_user, paused, theme="classic")

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


# ── Vote Skip embed (Tier-S Feature 1) ────────────────────────────────────────────────

def vote_skip_embed(
    track_title: str,
    votes:       set[int],
    threshold:   int,
    voters:      list[str],   # display names of voters
    color:       int = 0xF39C12,
) -> discord.Embed:
    """Live vote-skip progress embed with ASCII progress bar."""
    filled   = round((len(votes) / max(1, threshold)) * 10)
    filled   = min(filled, 10)
    bar      = "█" * filled + "░" * (10 - filled)
    pct      = int(len(votes) / max(1, threshold) * 100)

    lines = [
        f"**Track:** {truncate(track_title, 60)}",
        "",
        f"**Skip Votes**  `{len(votes)}/{threshold}`",
        f"`{bar}` {pct}%",
        "",
    ]
    if voters:
        voter_lines = [f"✅ {name}" for name in voters[:10]]
        remaining   = threshold - len(voters)
        for _ in range(min(remaining, 5)):
            voter_lines.append("⬜ *Waiting...*")
        lines.append("**Votes:**\n" + "\n".join(voter_lines))

    embed = discord.Embed(
        title       = "⏭️  Vote Skip",
        description = "\n".join(lines),
        color       = color,
    )
    embed.set_footer(text=f"React to vote • Expires in 60s")
    return embed


# ── Queue History embed (Tier-S Feature 4) ────────────────────────────────────────────

def history_embed(
    rows:    list[dict],
    guild_id: int,
    page:    int = 1,
    per_page: int = 10,
    color:   int = 0x9B59B6,
) -> discord.Embed:
    """Paginated play-history embed."""
    from models.track import Track as T
    total       = len(rows)
    total_pages = max(1, math.ceil(total / per_page))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page
    items       = rows[start:start + per_page]

    lines = []
    for i, row in enumerate(items, start + 1):
        try:
            t    = T.from_json(row["track_data"])
            when = row.get("played_at", "")[:16]  # trim to YYYY-MM-DD HH:MM
            flag = "⏭" if row.get("skipped") else ("✅" if row.get("completed") else "▶️")
            lines.append(
                f"`{i:>2}.` {flag} [{truncate(t.title, 48)}]({t.url}) `{t.duration_str}`\n"
                f"      └ `{when}`"
            )
        except Exception:
            pass

    embed = discord.Embed(
        title       = "🕐  Play History",
        description = "\n".join(lines) if lines else "*No history yet.*",
        color       = color,
    )
    embed.set_footer(text=f"Page {page}/{total_pages} · Total {total} tracks")
    return embed


# ── Queue Bookmark embeds (Tier-S Feature 5) ────────────────────────────────────────

def bookmark_list_embed(
    bookmarks: list[dict],
    user:      discord.User,
    color:     int = 0x1ABC9C,
) -> discord.Embed:
    embed = discord.Embed(
        title = f"📋  {user.display_name}'s Bookmarks ({len(bookmarks)})",
        color = color,
    )
    if bookmarks:
        lines = []
        for bm in bookmarks[:20]:
            when = str(bm.get("created_at", ""))[:10]
            lines.append(
                f"🔖 **{truncate(bm['name'], 40)}** — {bm['count']} tracks  `{when}`"
            )
        embed.description = "\n".join(lines)
    else:
        embed.description = "*No bookmarks yet. Use `/bookmark save <name>` to snapshot the queue!*"
    embed.set_footer(text="Max 20 bookmarks per user")
    return embed


def bookmark_saved_embed(name: str, count: int, color: int = 0x1ABC9C) -> discord.Embed:
    return discord.Embed(
        title       = "📋  Bookmark Saved",
        description = f"Snapshot **`{name}`** saved with **{count}** tracks.",
        color       = color,
    )


def bookmark_loaded_embed(name: str, count: int, mode: str, color: int = 0x1ABC9C) -> discord.Embed:
    action = "replaced" if mode == "replace" else "appended to"
    return discord.Embed(
        title       = "📋  Bookmark Loaded",
        description = f"**{count}** tracks from **`{name}`** {action} the queue.",
        color       = color,
    )


# ── Queue search embed (Tier-S Feature 7) ───────────────────────────────────────────

def queue_search_embed(
    query:   str,
    results: list[tuple[int, "Track"]],  # (1-based position, Track)
    color:   int = 0x5865F2,
) -> discord.Embed:
    """Embed showing queue search results with position numbers."""
    lines = []
    for pos, track in results[:15]:
        lines.append(
            f"`#{pos:>3}` [{truncate(track.title, 50)}]({track.url}) `{track.duration_str}`"
        )
    embed = discord.Embed(
        title       = f"🔍  Queue Search: `{truncate(query, 40)}`",
        description = "\n".join(lines) if lines else "*No matching tracks found in queue.*",
        color       = color,
    )
    embed.set_footer(text=f"{len(results)} result(s) found")
    return embed


# ── Queue Admin embeds (Features 2, 3, 6) ──────────────────────────────────────────────

def queue_lock_embed(locked: bool) -> discord.Embed:
    if locked:
        return discord.Embed(
            title       = "🔒  Queue Locked",
            description = "Only **DJ** and **Admin** can add tracks to the queue.",
            color       = 0xFF4757,
        )
    return discord.Embed(
        title       = "🔓  Queue Unlocked",
        description = "All users can now add tracks to the queue.",
        color       = 0x2ED573,
    )


def queue_permission_embed(level: str) -> discord.Embed:
    labels = {
        "everyone": "🌐 Everyone",
        "verified": "✅ Verified members",
        "dj":       "🎧 DJ role only",
        "admin":    "🔒 Admins only",
    }
    return discord.Embed(
        title       = "📝  Queue Permission Updated",
        description = f"Who can add tracks: **{labels.get(level, level)}**",
        color       = 0x5865F2,
    )


def duplicate_mode_embed(mode: str) -> discord.Embed:
    labels = {
        "allow": "✅ Allow — duplicates are added normally",
        "warn":  "⚠️ Warn — notified, but still added",
        "block": "🚫 Block — duplicates are rejected",
        "front": "⏫ Front — existing duplicate moved to front",
    }
    return discord.Embed(
        title       = "🔄  Duplicate Mode Updated",
        description = f"Mode: **{labels.get(mode, mode)}**",
        color       = 0x5865F2,
    )
