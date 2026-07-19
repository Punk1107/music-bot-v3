# -*- coding: utf-8 -*-
"""
utils/views.py — Discord UI Views (Buttons, Selects) for Music Bot V3.

V3 additions:
  - FavoriteView: quick ❤️ / ➕ Queue button on now-playing message
  - QueueManageSelect: dropdown in queue view to remove/move tracks
  - MusicControlView: updated with ❤️ favorite button
  - All buttons fully typed and state-synced
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Optional

import discord

from utils.embeds import (
    error_embed, success_embed, info_embed, queue_embed,
    now_playing_embed, favorite_added_embed,
)
from utils.formatters import truncate

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 10


# ─────────────────────────── Music Control View ───────────────────────────────

class MusicControlView(discord.ui.View):
    """
    Persistent playback-control bar shown under the now-playing embed.

    Row 0: ⏸/▶ Pause/Resume | ⏭⏭ Skip | 🔁 Loop | ✖ Shuffle | ⏹ Stop
    Row 1: 🔇 Vol-10%        | 🔊 Vol+10% | ❤️ Favorite
    """

    def __init__(self, bot: "MusicBot", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.bot      = bot
        self.guild_id = guild_id
        self._sync_buttons()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _vc(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        guild = self.bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None

    def _sync_buttons(self) -> None:
        player     = self.bot.get_player(self.guild_id)
        queue_size = len(player)
        is_playing = player.now_playing is not None

        # Detect paused state
        guild = self.bot.get_guild(self.guild_id)
        vc_client = guild.voice_client if guild else None
        is_paused = bool(vc_client and vc_client.is_paused())

        for child in self.children:
            if not hasattr(child, "custom_id"):
                continue
            cid = child.custom_id

            if cid == "mb_skip":
                skip_label     = f"⏭⏭ Skip" + (f" ({queue_size})" if queue_size else "")
                child.label    = skip_label
                child.disabled = not is_playing

            elif cid == "mb_shuffle":
                child.disabled = queue_size < 2

            elif cid == "mb_loop":
                mode = player.loop_mode.value
                child.label = f"🔁 Loop: {mode.capitalize()}"
                child.style = discord.ButtonStyle.primary

            elif cid == "mb_vol_down":
                child.disabled = player.volume <= 0.0

            elif cid == "mb_vol_up":
                child.disabled = player.volume >= 2.0

            elif cid == "mb_pause":
                if is_paused:
                    child.label = "▶ Resume"
                    child.style = discord.ButtonStyle.success
                else:
                    child.label = "⏸ Pause"
                    child.style = discord.ButtonStyle.secondary
                child.disabled = not is_playing

    async def _check(self, interaction: discord.Interaction) -> bool:
        """Verify user is in the same voice channel."""
        vc = self._vc(interaction)
        if not vc:
            await interaction.response.send_message(
                embed=error_embed("Not Connected", "I'm not in a voice channel."), ephemeral=True
            )
            return False
        if not interaction.user.voice:
            await interaction.response.send_message(
                embed=error_embed("Not in Voice", "Join a voice channel first."), ephemeral=True
            )
            return False
        if interaction.user.voice.channel != vc.channel:
            await interaction.response.send_message(
                embed=error_embed("Wrong Channel", f"Join **{vc.channel.name}** to use controls."), ephemeral=True
            )
            return False
        return True

    async def _refresh_message(self, interaction: discord.Interaction) -> None:
        """Synchronise controls and edit the interaction message.

        ``discord.ui.View`` already has a synchronous private ``_refresh``
        hook. Shadowing it with a coroutine causes an un-awaited coroutine
        warning while Discord deserialises a View.
        """
        self._sync_buttons()
        try:
            await interaction.response.edit_message(view=self)
        except discord.InteractionResponded:
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        except discord.NotFound:
            pass
        except Exception:
            pass

    # ── Row 0: Core controls ──────────────────────────────────────────────────

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.secondary, custom_id="mb_pause", row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        vc = self._vc(interaction)
        if vc and vc.is_playing():
            vc.pause()
        elif vc and vc.is_paused():
            vc.resume()
        await self._refresh_message(interaction)

    @discord.ui.button(label="⏭⏭ Skip", style=discord.ButtonStyle.primary, custom_id="mb_skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        await interaction.response.defer()
        vc = self._vc(interaction)
        if vc and vc.is_playing():
            vc.stop()
        self._sync_buttons()

    @discord.ui.button(label="🔁 Loop: Off", style=discord.ButtonStyle.primary, custom_id="mb_loop", row=0)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        player = self.bot.get_player(self.guild_id)
        player.loop_mode = player.loop_mode.next()
        await self._refresh_message(interaction)

    @discord.ui.button(label="✖ Shuffle", style=discord.ButtonStyle.secondary, custom_id="mb_shuffle", row=0)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        player = self.bot.get_player(self.guild_id)
        await player.shuffle()
        await interaction.response.send_message(
            embed=success_embed("Shuffled", "Queue has been shuffled."), ephemeral=True
        )

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger, custom_id="mb_stop", row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        vc = self._vc(interaction)
        player = self.bot.get_player(self.guild_id)
        player.reset()
        player.intentional_disconnect = True  # must come AFTER reset() so it sticks
        if vc:
            await vc.disconnect(force=True)
        await interaction.response.send_message(
            embed=success_embed("Stopped", "Playback stopped and queue cleared."), ephemeral=True
        )

    # ── Row 1: Volume + Favorite ──────────────────────────────────────────────

    @discord.ui.button(label="🔇 -10%", style=discord.ButtonStyle.secondary, custom_id="mb_vol_down", row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        player = self.bot.get_player(self.guild_id)
        player.volume = max(0.0, player.volume - 0.1)
        await self._refresh_message(interaction)

    @discord.ui.button(label="🔊 +10%", style=discord.ButtonStyle.secondary, custom_id="mb_vol_up", row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        player = self.bot.get_player(self.guild_id)
        player.volume = min(2.0, player.volume + 0.1)
        await self._refresh_message(interaction)

    @discord.ui.button(label="❤️ Favorite", style=discord.ButtonStyle.secondary, custom_id="mb_favorite", row=1)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Quick-save the currently playing track as a favorite."""
        player = self.bot.get_player(self.guild_id)
        if not player.now_playing:
            await interaction.response.send_message(
                embed=error_embed("Nothing Playing"), ephemeral=True
            )
            return
        track = player.now_playing
        # Use track title as default name
        name  = track.title[:50]
        user  = interaction.user
        ok    = await self.bot.db.add_favorite(user.id, self.guild_id, name, track)
        if ok:
            await interaction.response.send_message(
                embed=favorite_added_embed(name, track), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(
                    "Favorite Failed",
                    "Name already exists or favorites limit reached. Use `/favorite add <name>` with a custom name.",
                ),
                ephemeral=True,
            )



# ─────────────────────────── Queue View ──────────────────────────────────────

class QueueView(discord.ui.View):
    """Paginated queue display with ◀ ▶ navigation and track management."""

    def __init__(self, bot: "MusicBot", guild_id: int, page: int = 1) -> None:
        super().__init__(timeout=120)
        self.bot      = bot
        self.guild_id = guild_id
        self.page     = page
        self._sync_nav()

    def _total_pages(self) -> int:
        player = self.bot.get_player(self.guild_id)
        return max(1, math.ceil(len(player) / ITEMS_PER_PAGE))

    def _sync_nav(self) -> None:
        total = self._total_pages()
        for child in self.children:
            if not hasattr(child, "custom_id"):
                continue
            if child.custom_id == "q_prev":
                child.disabled = self.page <= 1
            elif child.custom_id == "q_next":
                child.disabled = self.page >= total

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="q_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(1, self.page - 1)
        self._sync_nav()
        player = self.bot.get_player(self.guild_id)
        color  = 0x5865F2
        embed  = queue_embed(player, self.page, ITEMS_PER_PAGE, color)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="q_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self._total_pages(), self.page + 1)
        self._sync_nav()
        player = self.bot.get_player(self.guild_id)
        color  = 0x5865F2
        embed  = queue_embed(player, self.page, ITEMS_PER_PAGE, color)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.primary, custom_id="q_shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.bot.get_player(self.guild_id)
        await player.shuffle()
        embed = queue_embed(player, self.page, ITEMS_PER_PAGE)
        self._sync_nav()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🗑 Clear", style=discord.ButtonStyle.danger, custom_id="q_clear")
    async def clear_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self.bot.get_player(self.guild_id)
        count  = await player.clear()
        await self.bot.db.clear_queue(self.guild_id)
        await interaction.response.edit_message(
            embed=success_embed("Queue Cleared", f"Removed {count} tracks."), view=None
        )


# ─────────────────────────── Search Select View ───────────────────────────────

class SearchSelectView(discord.ui.View):
    """Dropdown to select one of N search results."""

    def __init__(
        self,
        bot:      "MusicBot",
        guild_id: int,
        tracks:   list,
        callback,          # async callable(interaction, selected_track)
    ) -> None:
        super().__init__(timeout=60)
        self.bot      = bot
        self.guild_id = guild_id
        self.tracks   = tracks
        self._cb      = callback

        options = [
            discord.SelectOption(
                label       = truncate(t.title, 100),
                description = f"{t.duration_str} · {truncate(t.uploader or '', 50)}",
                value       = str(i),
            )
            for i, t in enumerate(tracks[:10])
        ]
        self.select = discord.ui.Select(
            placeholder = "Choose a track…",
            options     = options,
            custom_id   = "search_select",
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        index = int(self.select.values[0])
        track = self.tracks[index]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await self._cb(interaction, track)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


# ─────────────────────────── Favorites Pagination View ───────────────────────

class FavoritesView(discord.ui.View):
    """Paginated favorites list."""

    def __init__(
        self,
        bot:       "MusicBot",
        user:      discord.User,
        guild_id:  int,
        favorites: list[dict],
        page:      int = 1,
    ) -> None:
        super().__init__(timeout=90)
        self.bot       = bot
        self.user      = user
        self.guild_id  = guild_id
        self.favorites = favorites
        self.page      = page
        self._sync()

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.favorites) / ITEMS_PER_PAGE))

    def _sync(self) -> None:
        total = self._total_pages()
        for child in self.children:
            if not hasattr(child, "custom_id"):
                continue
            if child.custom_id == "fav_prev":
                child.disabled = self.page <= 1
            elif child.custom_id == "fav_next":
                child.disabled = self.page >= total

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="fav_prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from utils.embeds import favorites_list_embed
        self.page = max(1, self.page - 1)
        self._sync()
        embed = favorites_list_embed(self.favorites, self.user, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="fav_next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from utils.embeds import favorites_list_embed
        self.page = min(self._total_pages(), self.page + 1)
        self._sync()
        embed = favorites_list_embed(self.favorites, self.user, self.page)
        await interaction.response.edit_message(embed=embed, view=self)
