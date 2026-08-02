# -*- coding: utf-8 -*-
"""cogs/queue_cog.py — Queue management commands for Music Bot V3.

Tier-S additions:
  - /queue lock / /queue unlock       (Feature 2)
  - /queue permission <level>         (Feature 3)
  - /history                          (Feature 4)
  - /queue duplicates <mode>          (Feature 6)
  - /queue search <query>             (Feature 7)
  - /jump <position>                  (Feature 8)
  - /queue export <format>            (Feature 10)
  - /queue import                     (Feature 10)
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from models.enums import DuplicateMode, QueuePermission
from utils.embeds import (
    error_embed, success_embed, info_embed, queue_embed,
    history_embed, queue_search_embed,
    queue_lock_embed, queue_permission_embed, duplicate_mode_embed,
)
from utils.views import QueueView, HistoryView, QueueSearchResultView
from utils.error_handler import dj_required_embed
from utils.color_thief import get_dominant_color
from utils.formatters import format_duration, truncate

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class QueueCog(commands.Cog, name="Queue"):
    """Queue management commands."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    # ── Permission helpers ────────────────────────────────────────────────────

    async def _check_dj(self, interaction: discord.Interaction) -> bool:
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True
        member = interaction.user
        if member.guild_permissions.administrator:
            return True
        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True
        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    # ── Core Queue Commands ───────────────────────────────────────────────────

    @app_commands.command(name="queue", description="Show the current queue")
    @app_commands.describe(page="Page number (default: 1)")
    async def queue_cmd(self, interaction: discord.Interaction, page: int = 1) -> None:
        await interaction.response.defer()
        player = self.bot.get_player(interaction.guild_id)
        now    = player.now_playing
        color  = await get_dominant_color(now.thumbnail if now else None, self.bot.http_session)
        embed  = queue_embed(player, page, color=color)
        view   = QueueView(self.bot, interaction.guild_id, page)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        if len(player) < 2:
            await interaction.followup.send(
                embed=error_embed("Not Enough Tracks", "Need at least 2 tracks to shuffle."), ephemeral=True
            )
            return
        player.undo_push("shuffle")    # F19: snapshot before mutation
        await player.shuffle()
        await interaction.followup.send(embed=success_embed("Shuffled 🔀", f"Shuffled {len(player)} tracks."))

    @app_commands.command(name="clear", description="Clear the entire queue")
    async def clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        player.undo_push("clear")       # F19: snapshot before mutation
        count  = await player.clear()
        await self.bot.db.clear_queue(interaction.guild_id)
        await interaction.followup.send(embed=success_embed("Queue Cleared", f"Removed {count} tracks."))

    @app_commands.command(name="loop", description="Cycle loop mode: Off → Track → Queue")
    async def loop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player        = self.bot.get_player(interaction.guild_id)
        player.loop_mode = player.loop_mode.next()
        await interaction.followup.send(
            embed=success_embed("Loop Mode", player.loop_mode.label()), ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove a track by position")
    @app_commands.describe(position="1-based position in the queue")
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player  = self.bot.get_player(interaction.guild_id)
        player.undo_push("remove", extra=position)  # F19: snapshot before mutation
        removed = await player.remove(position - 1)
        if removed:
            await interaction.followup.send(
                embed=success_embed("Removed", f"Removed **{removed.short_title}** from position {position}."),
                ephemeral=True,
            )
            vc = interaction.guild.voice_client
            if vc:
                asyncio.create_task(
                    self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
                )
        else:
            # Pop the undo entry since nothing was removed
            player.undo_pop()
            await interaction.followup.send(
                embed=error_embed("Invalid Position", f"No track at position {position}."), ephemeral=True
            )

    @app_commands.command(name="move", description="Move a track to a new position")
    @app_commands.describe(
        from_pos="Current position (1-based)",
        to_pos  ="Target position (1-based)",
    )
    async def move(self, interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player  = self.bot.get_player(interaction.guild_id)
        player.undo_push("move", extra=(from_pos, to_pos))  # F19: snapshot before mutation
        ok = await player.move(from_pos - 1, to_pos - 1)
        if ok:
            await interaction.followup.send(
                embed=success_embed("Moved", f"Track moved from position {from_pos} → {to_pos}."),
                ephemeral=True,
            )
        else:
            player.undo_pop()  # nothing moved — discard snapshot
            await interaction.followup.send(
                embed=error_embed("Invalid Position", "Check both positions are within queue range."), ephemeral=True
            )

    # ── Feature 2: Queue Lock ─────────────────────────────────────────────────

    @app_commands.command(name="queuelock", description="Lock or unlock the queue (Admin only)")
    @app_commands.describe(locked="true = lock, false = unlock")
    async def queuelock(self, interaction: discord.Interaction, locked: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self._is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Only **Admins** can lock the queue."), ephemeral=True
            )
            return
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.queue_locked = locked
        await self.bot.db.save_server_config(cfg)
        await interaction.followup.send(embed=queue_lock_embed(locked), ephemeral=True)

    # ── Feature 3: Queue Permission ───────────────────────────────────────────

    @app_commands.command(name="queueperm", description="Set who can add tracks to the queue (Admin only)")
    @app_commands.describe(level="Permission level: everyone / verified / dj / admin")
    @app_commands.choices(level=[
        app_commands.Choice(name="🌐 Everyone",            value="everyone"),
        app_commands.Choice(name="✅ Verified (has a role)", value="verified"),
        app_commands.Choice(name="🎧 DJ only",              value="dj"),
        app_commands.Choice(name="🔒 Admin only",           value="admin"),
    ])
    async def queueperm(self, interaction: discord.Interaction, level: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self._is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", "Only **Admins** can change queue permissions."), ephemeral=True
            )
            return
        try:
            perm = QueuePermission(level)
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Invalid Level", f"Choose from: everyone, verified, dj, admin"), ephemeral=True
            )
            return
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.queue_add_permission = perm
        await self.bot.db.save_server_config(cfg)
        await interaction.followup.send(embed=queue_permission_embed(level), ephemeral=True)

    # ── Feature 4: Queue History ──────────────────────────────────────────────

    @app_commands.command(name="qhistory", description="Show recently played tracks with replay buttons")
    @app_commands.describe(limit="Number of tracks to show (default: 20, max: 50)")
    async def qhistory(self, interaction: discord.Interaction, limit: int = 20) -> None:
        await interaction.response.defer()
        limit = max(1, min(limit, 50))
        rows  = await self.bot.db.get_history(interaction.guild_id, limit=limit)

        if not rows:
            await interaction.followup.send(embed=info_embed("No History", "No tracks have been played yet."))
            return

        # Callback: replay a track from history
        async def replay_track(inter: discord.Interaction, track) -> None:
            music_cog = self.bot.get_cog("Music")
            if music_cog:
                await music_cog.play_track(inter, track)
            else:
                await inter.followup.send(embed=error_embed("Error", "Music cog unavailable."), ephemeral=True)

        embed = history_embed(rows, interaction.guild_id, page=1, per_page=5)
        view  = HistoryView(self.bot, interaction.guild_id, rows, replay_track)
        await interaction.followup.send(embed=embed, view=view)

    # ── Feature 6: Smart Duplicate Detection ──────────────────────────────────

    @app_commands.command(name="duplicates", description="Set how duplicate tracks are handled (Admin/DJ)")
    @app_commands.describe(mode="Duplicate handling mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="✅ Allow — add normally",            value="allow"),
        app_commands.Choice(name="⚠️ Warn — notify but still add",     value="warn"),
        app_commands.Choice(name="🚫 Block — reject duplicates",       value="block"),
        app_commands.Choice(name="⏫ Front — move existing to front",  value="front"),
    ])
    async def duplicates(self, interaction: discord.Interaction, mode: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        try:
            dup_mode = DuplicateMode(mode)
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Invalid Mode", "Choose from: allow, warn, block, front"), ephemeral=True
            )
            return
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        cfg.duplicate_mode = dup_mode
        await self.bot.db.save_server_config(cfg)
        await interaction.followup.send(embed=duplicate_mode_embed(mode), ephemeral=True)

    # ── Feature 7: Queue Search ───────────────────────────────────────────────

    @app_commands.command(name="qsearch", description="Search for a track in the current queue")
    @app_commands.describe(query="Track title to search for")
    async def qsearch(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()
        player  = self.bot.get_player(interaction.guild_id)
        queue   = player.queue
        q_lower = query.strip().lower()

        results: list[tuple[int, object]] = [
            (i + 1, track)
            for i, track in enumerate(queue)
            if q_lower in track.title.lower()
        ]

        if not results:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"No tracks matching **{query}** in queue."), ephemeral=True
            )
            return

        # Jump callback
        async def do_jump(inter: discord.Interaction, pos: int) -> None:
            target = await player.jump_to(pos - 1)
            if not target:
                await inter.followup.send(embed=error_embed("Jump Failed", "Position no longer valid."), ephemeral=True)
                return
            vc = inter.guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()  # triggers _play_next via after_play callback
            await inter.followup.send(
                embed=success_embed("Jumped ↪", f"Jumping to **{target.short_title}**."), ephemeral=True
            )

        # Remove callback
        async def do_remove(inter: discord.Interaction, pos: int) -> None:
            removed = await player.remove(pos - 1)
            if removed:
                await inter.followup.send(
                    embed=success_embed("Removed 🗑", f"Removed **{removed.short_title}** from queue."), ephemeral=True
                )
            else:
                await inter.followup.send(embed=error_embed("Remove Failed", "Position no longer valid."), ephemeral=True)

        embed = queue_search_embed(query, results)
        view  = QueueSearchResultView(self.bot, interaction.guild_id, results, do_jump, do_remove)
        await interaction.followup.send(embed=embed, view=view)

    # ── Feature 8: Queue Jump ─────────────────────────────────────────────────

    @app_commands.command(name="jump", description="Jump directly to a position in the queue (DJ/Admin)")
    @app_commands.describe(position="1-based position to jump to")
    async def jump(self, interaction: discord.Interaction, position: int) -> None:
        await interaction.response.defer()
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        target = await player.jump_to(position - 1)

        if not target:
            await interaction.followup.send(
                embed=error_embed("Invalid Position", f"No track at position {position}."), ephemeral=True
            )
            return

        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()  # after_play fires _play_next which picks up the jumped-to track

        await interaction.followup.send(
            embed=success_embed("Jumped ↪", f"Jumping to **{target.short_title}** (was position #{position})."),
            ephemeral=True,
        )

    # ── Feature 10: Queue Export ──────────────────────────────────────────────

    @app_commands.command(name="qexport", description="Export the current queue as a file")
    @app_commands.describe(fmt="Export format: json / csv / txt")
    @app_commands.choices(fmt=[
        app_commands.Choice(name="JSON", value="json"),
        app_commands.Choice(name="CSV",  value="csv"),
        app_commands.Choice(name="TXT",  value="txt"),
    ])
    async def qexport(self, interaction: discord.Interaction, fmt: str = "json") -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.bot.get_player(interaction.guild_id)
        queue  = player.queue

        if not queue:
            await interaction.followup.send(embed=error_embed("Empty Queue", "The queue is empty."), ephemeral=True)
            return

        filename = f"queue_{interaction.guild_id}.{fmt}"

        if fmt == "json":
            data    = [t.to_dict() for t in queue]
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

        elif fmt == "csv":
            buf    = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["position", "title", "url", "duration", "uploader", "requested_by"])
            for i, t in enumerate(queue, 1):
                writer.writerow([i, t.title, t.url, t.duration_str,
                                  t.uploader or "", t.requested_by_name or ""])
            content = buf.getvalue().encode("utf-8")

        else:  # txt
            lines = [f"Music Bot Queue Export — {interaction.guild.name}", "=" * 50]
            for i, t in enumerate(queue, 1):
                req = f" (by {t.requested_by_name})" if t.requested_by_name else ""
                lines.append(f"{i:>3}. [{t.duration_str}] {t.title}{req}")
                lines.append(f"      {t.url}")
            content = "\n".join(lines).encode("utf-8")

        file = discord.File(io.BytesIO(content), filename=filename)
        await interaction.followup.send(
            embed=success_embed(
                "Queue Exported 📤",
                f"**{len(queue)}** tracks exported as `{filename}`.",
            ),
            file=file,
            ephemeral=True,
        )

    # ── Feature 10: Queue Import ──────────────────────────────────────────────

    @app_commands.command(name="qimport", description="Import a queue from a JSON file attachment")
    @app_commands.describe(mode="replace = clear existing, append = add to queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Replace queue", value="replace"),
        app_commands.Choice(name="Append to queue", value="append"),
    ])
    async def qimport(self, interaction: discord.Interaction, mode: str = "append") -> None:
        await interaction.response.defer(ephemeral=True)

        # Fetch the attachment from the last message in channel that has a JSON file
        # Discord slash commands don't support attachments natively in older API versions,
        # so we use a workaround: look at the interaction message's resolved attachments,
        # or instruct the user to attach via a follow-up.
        # discord.py 2.x supports Attachment parameters directly:
        await interaction.followup.send(
            embed=info_embed(
                "Import Instructions",
                "Please use `/qimport_file` and attach your JSON file exported from `/qexport`.\n\n"
                "*Tip: Export first with `/qexport json` then import with `/qimport_file`.*"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="qimport_file", description="Import queue from attached JSON file")
    @app_commands.describe(
        file="JSON file exported from /qexport",
        mode="replace = clear existing, append = add to queue",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Replace queue", value="replace"),
        app_commands.Choice(name="Append to queue", value="append"),
    ])
    async def qimport_file(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        mode: str = "append",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        if not file.filename.endswith(".json"):
            await interaction.followup.send(
                embed=error_embed("Wrong Format", "Only `.json` files from `/qexport` are supported."), ephemeral=True
            )
            return

        if file.size > 500_000:  # 500 KB limit
            await interaction.followup.send(
                embed=error_embed("File Too Large", "File must be under 500 KB."), ephemeral=True
            )
            return

        try:
            raw  = await file.read()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("Root must be a JSON array")
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed("Parse Error", f"Could not parse file: {exc}"), ephemeral=True
            )
            return

        from models.track import Track
        tracks = []
        for item in data:
            try:
                tracks.append(Track.from_dict(item))
            except Exception:
                continue

        if not tracks:
            await interaction.followup.send(
                embed=error_embed("No Tracks", "No valid tracks found in the file."), ephemeral=True
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        vc     = interaction.guild.voice_client

        if mode == "replace":
            await player.clear()

        await player.extend(tracks)

        if vc:
            asyncio.create_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )

        await interaction.followup.send(
            embed=success_embed(
                f"Queue {'Replaced' if mode == 'replace' else 'Updated'} 📥",
                f"Imported **{len(tracks)}** tracks from `{file.filename}`.",
            ),
            ephemeral=True,
        )

        # Start playback if nothing is playing
        if vc and not vc.is_playing() and not vc.is_paused():
            music_cog = self.bot.get_cog("Music")
            if music_cog:
                await music_cog._play_next(interaction.guild_id)


    # ── Feature 19: Queue Undo ────────────────────────────────────────────────

    @app_commands.command(name="undo", description="Undo the last queue operation (shuffle/clear/remove/move)")
    async def undo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        entry  = player.undo_pop()

        if not entry:
            await interaction.followup.send(
                embed=error_embed("Nothing to Undo", "The undo stack is empty."),
                ephemeral=True,
            )
            return

        await player.apply_undo(entry)

        # Restore DB queue
        vc = interaction.guild.voice_client
        if vc:
            asyncio.create_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )

        # Build friendly description
        op_labels = {
            "shuffle": "🔀 Shuffle",
            "clear":   "🗑️ Clear Queue",
            "remove":  "✖ Remove",
            "move":    "↕ Move",
        }
        label = op_labels.get(entry.operation, entry.operation.title())
        desc  = (
            f"Undid **{label}** — restored **{len(entry.snapshot)}** track(s).\n"
            f"*(Undo stack: {len(player.undo_stack)} entries remaining)*"
        )
        embed = discord.Embed(
            title       = "↩️  Undo Successful",
            description = desc,
            color       = 0x2ED573,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Feature 20: Queue Transaction ─────────────────────────────────────────

    _transaction_group = app_commands.Group(
        name        = "qtransaction",
        description = "Atomic queue transactions — begin / commit / rollback",
    )

    @_transaction_group.command(name="begin", description="Start a queue transaction (snapshot current state)")
    async def qtx_begin(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        ok = player.begin_transaction()
        if not ok:
            await interaction.followup.send(
                embed=error_embed(
                    "Transaction Already Open",
                    "Call `/qtransaction rollback` or `/qtransaction commit` first.",
                ),
                ephemeral=True,
            )
            return
        n = len(player.queue)
        embed = discord.Embed(
            title       = "🔒  Transaction Begun",
            description = (
                f"Snapshotted **{n}** track(s).\n"
                "You can now run queue operations safely.\n"
                "Use `/qtransaction commit` to finalise or `/qtransaction rollback` to undo all."
            ),
            color       = 0x70A1FF,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @_transaction_group.command(name="commit", description="Commit the current transaction (keep all changes)")
    async def qtx_commit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        ok = player.commit_transaction()
        if not ok:
            await interaction.followup.send(
                embed=error_embed("No Transaction Open", "Start one with `/qtransaction begin`."),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title       = "✅  Transaction Committed",
            description = f"Changes finalised. Queue now has **{len(player.queue)}** track(s).",
            color       = 0x2ED573,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @_transaction_group.command(name="rollback", description="Rollback the current transaction (revert all changes)")
    async def qtx_rollback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return
        player = self.bot.get_player(interaction.guild_id)
        ok = await player.rollback_transaction()
        if not ok:
            await interaction.followup.send(
                embed=error_embed("No Transaction Open", "Start one with `/qtransaction begin`."),
                ephemeral=True,
            )
            return
        # Restore DB
        vc = interaction.guild.voice_client
        if vc:
            asyncio.create_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )
        embed = discord.Embed(
            title       = "♻️  Transaction Rolled Back",
            description = (
                f"All changes since `begin` were reverted.\n"
                f"Queue restored to **{len(player.queue)}** track(s)."
            ),
            color       = 0xFF4757,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    cog = QueueCog(bot)
    bot.tree.add_command(cog._transaction_group)
    await bot.add_cog(cog)
