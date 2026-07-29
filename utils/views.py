# -*- coding: utf-8 -*-
"""
utils/views.py — Discord UI Views (Buttons, Selects) for Music Bot V3.

V3 additions:
  - FavoriteView: quick ❤️ / ➕ Queue button on now-playing message
  - QueueManageSelect: dropdown in queue view to remove/move tracks
  - MusicControlView: updated with ❤️ favorite button
  - All buttons fully typed and state-synced

Tier-S additions:
  - VoteSkipView: live voting UI with progress bar (Feature 1)
  - HistoryView: paginated history with Replay buttons (Feature 4)
  - QueueSearchResultView: Jump/Remove/Move buttons on search results (Feature 7)
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
    vote_skip_embed, history_embed, queue_search_embed,
)
from utils.formatters import truncate, format_duration

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

        # Detect actual voice client state
        guild = self.bot.get_guild(self.guild_id)
        vc_client = guild.voice_client if guild else None
        is_paused  = bool(vc_client and vc_client.is_paused())
        vc_active  = bool(vc_client and (vc_client.is_playing() or vc_client.is_paused()))
        # Fallback: treat as active if player thinks something is playing
        is_playing = vc_active or (player.now_playing is not None)

        for child in self.children:
            if not hasattr(child, "custom_id"):
                continue
            cid = child.custom_id

            if cid == "mb_skip":
                skip_label     = f"⏭ Skip" + (f" ({queue_size})" if queue_size else "")
                child.label    = skip_label
                child.disabled = not is_playing

            elif cid == "mb_shuffle":
                child.disabled = queue_size < 2

            elif cid == "mb_loop":
                mode = player.loop_mode.value
                if mode == "off":
                    child.label = "🔁 Loop: Off"
                    child.style = discord.ButtonStyle.secondary
                elif mode == "track":
                    child.label = "🔂 Loop: Track"
                    child.style = discord.ButtonStyle.primary
                else:
                    child.label = "🔁 Loop: Queue"
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
        if not vc:
            await interaction.response.send_message(
                embed=error_embed("Not Connected", "Not in a voice channel."), ephemeral=True
            )
            return
        if vc.is_playing():
            vc.pause()
        elif vc.is_paused():
            vc.resume()
        else:
            await interaction.response.send_message(
                embed=error_embed("Nothing Playing", "There is nothing to pause or resume."), ephemeral=True
            )
            return
        await self._refresh_message(interaction)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.primary, custom_id="mb_skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        await interaction.response.defer()
        vc = self._vc(interaction)
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        self._sync_buttons()

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary, custom_id="mb_loop", row=0)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._check(interaction):
            return
        player = self.bot.get_player(self.guild_id)
        player.loop_mode = player.loop_mode.next()
        await self._refresh_message(interaction)

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.secondary, custom_id="mb_shuffle", row=0)
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
        if vc and vc.is_playing():
            vc.stop()
        player.reset()
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

    @discord.ui.button(label="◄", style=discord.ButtonStyle.secondary, custom_id="fav_prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from utils.embeds import favorites_list_embed
        self.page = max(1, self.page - 1)
        self._sync()
        embed = favorites_list_embed(self.favorites, self.user, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="►", style=discord.ButtonStyle.secondary, custom_id="fav_next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from utils.embeds import favorites_list_embed
        self.page = min(self._total_pages(), self.page + 1)
        self._sync()
        embed = favorites_list_embed(self.favorites, self.user, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


# ─────────────────────────── Vote Skip View (Tier-S #1) ────────────────────────────

class VoteSkipView(discord.ui.View):
    """
    Interactive vote-skip panel.
    - Shows real-time vote count + ASCII progress bar.
    - Auto-expires after 60 seconds.
    - DJ/Admin bypass triggers immediate skip.
    """

    def __init__(
        self,
        bot:       "MusicBot",
        guild_id:  int,
        threshold: int,
        track_title: str,
    ) -> None:
        super().__init__(timeout=60)
        self.bot         = bot
        self.guild_id    = guild_id
        self.threshold   = threshold
        self.track_title = track_title
        self._message: Optional[discord.Message] = None

    def set_message(self, msg: discord.Message) -> None:
        self._message = msg

    def _voter_names(self, guild: discord.Guild) -> list[str]:
        player = self.bot.get_player(self.guild_id)
        names = []
        for uid in player.skip_votes:
            member = guild.get_member(uid)
            names.append(member.display_name if member else f"User#{uid}")
        return names

    async def _refresh(self, interaction: discord.Interaction) -> None:
        player = self.bot.get_player(self.guild_id)
        guild  = self.bot.get_guild(self.guild_id)
        embed  = vote_skip_embed(
            track_title = self.track_title,
            votes       = player.skip_votes,
            threshold   = self.threshold,
            voters      = self._voter_names(guild) if guild else [],
        )
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.InteractionResponded:
            if self._message:
                await self._message.edit(embed=embed, view=self)

    @discord.ui.button(label="⏭️ Vote Skip", style=discord.ButtonStyle.danger, custom_id="vs_vote")
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player  = self.bot.get_player(self.guild_id)
        user_id = interaction.user.id
        guild   = self.bot.get_guild(self.guild_id)
        vc      = guild.voice_client if guild else None

        # Must be in the same voice channel
        if not interaction.user.voice or (vc and interaction.user.voice.channel != vc.channel):
            await interaction.response.send_message(
                embed=error_embed("Not in Voice", "Join the voice channel to vote."), ephemeral=True
            )
            return

        if user_id in player.skip_votes:
            await interaction.response.send_message(
                embed=error_embed("Already Voted", "You already voted to skip this track."), ephemeral=True
            )
            return

        player.skip_votes.add(user_id)
        await self._refresh(interaction)

        # Check threshold
        if len(player.skip_votes) >= self.threshold:
            self.stop()
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            if self._message:
                await self._message.edit(
                    embed=success_embed("Skipped! ⏭", f"Vote threshold reached ({self.threshold}/{self.threshold})."),
                    view=None,
                )

    @discord.ui.button(label="❌ Cancel Vote", style=discord.ButtonStyle.secondary, custom_id="vs_cancel")
    async def cancel_vote(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player  = self.bot.get_player(self.guild_id)
        user_id = interaction.user.id

        if user_id not in player.skip_votes:
            await interaction.response.send_message(
                embed=error_embed("Not Voted", "You haven't voted to skip."), ephemeral=True
            )
            return

        player.skip_votes.discard(user_id)
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        if self._message:
            try:
                await self._message.edit(
                    embed=info_embed("Vote Expired", "The vote-skip poll expired without enough votes."),
                    view=None,
                )
            except Exception:
                pass


# ─────────────────────────── History View (Tier-S #4) ─────────────────────────────

class HistoryView(discord.ui.View):
    """Paginated play-history view with Replay buttons."""

    def __init__(
        self,
        bot:      "MusicBot",
        guild_id: int,
        rows:     list[dict],
        play_cb,              # async callable(interaction, Track)
        page:     int = 1,
    ) -> None:
        super().__init__(timeout=120)
        self.bot      = bot
        self.guild_id = guild_id
        self.rows     = rows
        self._cb      = play_cb
        self.page     = page
        self._rebuild()

    ITEMS_PER_PAGE = 5

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.rows) / self.ITEMS_PER_PAGE))

    def _rebuild(self) -> None:
        """Rebuild buttons: nav + replay buttons for current page items."""
        self.clear_items()
        from models.track import Track as T

        total = self._total_pages()
        start = (self.page - 1) * self.ITEMS_PER_PAGE
        items = self.rows[start:start + self.ITEMS_PER_PAGE]

        # Replay buttons (row 0-2)
        for idx, row in enumerate(items):
            try:
                track = T.from_json(row["track_data"])
            except Exception:
                continue
            btn = discord.ui.Button(
                label    = f"🔄 {truncate(track.title, 40)}",
                style    = discord.ButtonStyle.primary,
                custom_id= f"hist_replay_{start + idx}",
                row      = idx % 4,
            )
            # Capture loop variable
            def make_callback(t: T):
                async def _cb(inter: discord.Interaction, _btn: discord.ui.Button) -> None:
                    await inter.response.defer()
                    await self._cb(inter, t)
                return _cb
            btn.callback = make_callback(track)
            self.add_item(btn)

        # Nav buttons (last row)
        prev_btn = discord.ui.Button(
            label    = "◄",
            style    = discord.ButtonStyle.secondary,
            custom_id= "hist_prev",
            disabled = self.page <= 1,
            row      = 4,
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label    = "►",
            style    = discord.ButtonStyle.secondary,
            custom_id= "hist_next",
            disabled = self.page >= total,
            row      = 4,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

    async def _prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(1, self.page - 1)
        self._rebuild()
        embed = history_embed(self.rows, self.guild_id, self.page, self.ITEMS_PER_PAGE)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self._total_pages(), self.page + 1)
        self._rebuild()
        embed = history_embed(self.rows, self.guild_id, self.page, self.ITEMS_PER_PAGE)
        await interaction.response.edit_message(embed=embed, view=self)


# ─────────────────────────── Queue Search Result View (Tier-S #7) ────────────────────

class QueueSearchResultView(discord.ui.View):
    """
    Shows up to 5 queue-search results with Jump / Remove buttons per track.
    """

    def __init__(
        self,
        bot:      "MusicBot",
        guild_id: int,
        results:  list[tuple[int, object]],  # (1-based position, Track)
        jump_cb,                             # async callable(interaction, 1-based pos)
        remove_cb,                           # async callable(interaction, 1-based pos)
    ) -> None:
        super().__init__(timeout=60)
        self.bot       = bot
        self.guild_id  = guild_id
        self.results   = results
        self._jump_cb  = jump_cb
        self._rm_cb    = remove_cb
        self._build()

    def _build(self) -> None:
        self.clear_items()
        for row_idx, (pos, track) in enumerate(self.results[:4]):
            jump_btn = discord.ui.Button(
                label    = f"↪ #{pos}",
                style    = discord.ButtonStyle.primary,
                custom_id= f"qs_jump_{pos}",
                row      = row_idx,
            )
            rm_btn = discord.ui.Button(
                label    = f"🗑 Remove",
                style    = discord.ButtonStyle.danger,
                custom_id= f"qs_rm_{pos}",
                row      = row_idx,
            )

            def make_jump(p: int):
                async def _j(inter: discord.Interaction, _btn: discord.ui.Button) -> None:
                    await inter.response.defer()
                    await self._jump_cb(inter, p)
                return _j

            def make_rm(p: int):
                async def _r(inter: discord.Interaction, _btn: discord.ui.Button) -> None:
                    await inter.response.defer()
                    await self._rm_cb(inter, p)
                return _r

            jump_btn.callback = make_jump(pos)
            rm_btn.callback   = make_rm(pos)
            self.add_item(jump_btn)
            self.add_item(rm_btn)
