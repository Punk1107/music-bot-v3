# -*- coding: utf-8 -*-
"""playlist/service.py — Multi-Playlist Personal Manager business logic (Gen 5.0)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import discord

from utils.formatters import truncate, format_duration

if TYPE_CHECKING:
    from main import MusicBot
    from models.track import Track


class PlaylistService:
    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    def build_playlist_list_embed(self, playlists: list[dict], user: discord.User) -> discord.Embed:
        """Construct embed for viewing user and public guild playlists."""
        embed = discord.Embed(
            title=f"📁 Playlists — {user.display_name}",
            color=0x5865F2,
        )
        if not playlists:
            embed.description = "No playlists found! Create one with `/playlist create <name>`."
            return embed

        lines = []
        for pl in playlists:
            status = "🌐 Public" if pl.get("is_public") else "🔒 Private"
            owner_tag = " (Yours)" if pl.get("user_id") == user.id else f" (by <@{pl.get('user_id')}>)"
            desc = f" — *{truncate(pl['description'], 40)}*" if pl.get("description") else ""
            lines.append(
                f"• **{pl['name']}**{desc}\n"
                f"  └ `{pl['count']} tracks` • `{status}` • Plays: `{pl.get('play_count', 0)}`{owner_tag}"
            )

        embed.description = "\n\n".join(lines[:15])
        embed.set_footer(text="Play any playlist using /playlist play <name>")
        return embed

