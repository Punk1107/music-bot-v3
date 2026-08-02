# -*- coding: utf-8 -*-
"""
cogs/playback_cog.py — Advanced playback controls for Music Bot V3.

Tier A+ Features:
  F21: /speed  — Playback speed (0.75x, 1.0x, 1.25x, 1.5x, 2.0x)
  F22: /pitch  — Pitch shift in semitones (-2, -1, 0, +1, +2)
  F23: /crossfade — Crossfade duration between tracks (off, 3s, 5s, 8s)
  F24: /silencetrim — Toggle silence trim (remove leading/trailing silence)
  F25: /replaygain  — Toggle replay gain / loudness normalization

All effects are applied purely via FFmpeg — no external APIs required.
Changing any setting restarts the current FFmpeg process so it takes effect
immediately on the playing track.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class PlaybackCog(commands.Cog, name="Playback"):
    """Advanced playback control commands (speed, pitch, crossfade, silence trim, replay gain)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_dj(self, interaction: discord.Interaction) -> bool:
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True
        member = interaction.user
        if member.guild_permissions.administrator:
            return True
        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True
        from utils.error_handler import dj_required_embed
        await interaction.followup.send(embed=dj_required_embed(), ephemeral=True)
        return False

    def _restart_audio(self, guild_id: int) -> None:
        """
        Stop FFmpeg so the after_play callback fires _play_next,
        which will pick up the new settings when rebuilding FFmpeg options.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        vc = guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    # ── F21: Playback Speed ───────────────────────────────────────────────────

    @app_commands.command(name="speed", description="Set playback speed (pure FFmpeg atempo — no pitch change)")
    @app_commands.describe(rate="Speed multiplier")
    @app_commands.choices(rate=[
        app_commands.Choice(name="0.75x  (Slowed)",       value=0.75),
        app_commands.Choice(name="1.0x   (Normal)",        value=1.0),
        app_commands.Choice(name="1.25x  (Slightly Fast)", value=1.25),
        app_commands.Choice(name="1.5x   (Fast)",          value=1.5),
        app_commands.Choice(name="2.0x   (Double Speed)",  value=2.0),
    ])
    async def speed(self, interaction: discord.Interaction, rate: float) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        old    = player.playback_speed
        player.playback_speed = rate

        if abs(rate - old) > 0.001:
            self._restart_audio(interaction.guild_id)

        speed_label = f"{rate}x"
        emoji = "🐢" if rate < 1.0 else ("🚀" if rate > 1.0 else "▶️")
        embed = discord.Embed(
            title       = f"{emoji}  Playback Speed",
            description = (
                f"Speed set to **{speed_label}**\n"
                f"*Pitch is unchanged — pure `atempo` FFmpeg chain*"
            ),
            color       = 0x5865F2,
        )
        if player.now_playing:
            embed.set_footer(text=f"Restarting: {player.now_playing.short_title}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── F22: Pitch Shift ──────────────────────────────────────────────────────

    @app_commands.command(name="pitch", description="Shift pitch in semitones (speed stays constant via atempo correction)")
    @app_commands.describe(semitones="Semitone shift (negative = lower, positive = higher)")
    @app_commands.choices(semitones=[
        app_commands.Choice(name="-2  (Much Lower)",   value=-2),
        app_commands.Choice(name="-1  (Lower)",         value=-1),
        app_commands.Choice(name=" 0  (Normal)",        value=0),
        app_commands.Choice(name="+1  (Higher)",        value=1),
        app_commands.Choice(name="+2  (Much Higher)",   value=2),
    ])
    async def pitch(self, interaction: discord.Interaction, semitones: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        old    = player.pitch_semitones
        player.pitch_semitones = semitones

        if semitones != old:
            self._restart_audio(interaction.guild_id)

        sign  = "+" if semitones > 0 else ""
        emoji = "🎵" if semitones == 0 else ("🔼" if semitones > 0 else "🔽")
        embed = discord.Embed(
            title       = f"{emoji}  Pitch Shift",
            description = (
                f"Pitch set to **{sign}{semitones} semitone(s)**\n"
                f"*Speed stays constant via `asetrate + atempo` correction*"
            ),
            color       = 0xFF6B81,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── F23: Crossfade ────────────────────────────────────────────────────────

    @app_commands.command(name="crossfade", description="Fade between tracks smoothly (fade-out + fade-in via FFmpeg)")
    @app_commands.describe(seconds="Crossfade duration in seconds (0 = disabled)")
    @app_commands.choices(seconds=[
        app_commands.Choice(name="Off   (No crossfade)",  value=0),
        app_commands.Choice(name="3s    (Short)",          value=3),
        app_commands.Choice(name="5s    (Medium)",         value=5),
        app_commands.Choice(name="8s    (Long / Smooth)",  value=8),
    ])
    async def crossfade(self, interaction: discord.Interaction, seconds: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        player.crossfade_seconds = seconds

        if seconds == 0:
            desc  = "Crossfade **disabled**. Tracks switch instantly."
            color = 0x2F3136
        else:
            desc  = (
                f"Crossfade set to **{seconds}s**.\n"
                f"Each track fades out over `{seconds}s`, and the next fades in — applied via `afade` FFmpeg filter."
            )
            color = 0x1DB954

        embed = discord.Embed(
            title       = "🎚️  Crossfade",
            description = desc,
            color       = color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── F24: Silence Trim ─────────────────────────────────────────────────────

    @app_commands.command(name="silencetrim", description="Toggle automatic silence removal from track intro/outro")
    async def silencetrim(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        player.silence_trim = not player.silence_trim
        state = player.silence_trim

        self._restart_audio(interaction.guild_id)

        embed = discord.Embed(
            title       = "✂️  Silence Trim",
            description = (
                f"Silence trim is now **{'enabled ✅' if state else 'disabled ❌'}**.\n"
                + (
                    "*Leading and trailing silence will be removed via `silenceremove` FFmpeg filter.*"
                    if state else
                    "*Tracks play as-is, including any intro/outro silence.*"
                )
            ),
            color       = 0x2ED573 if state else 0x747F8D,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── F25: Replay Gain / Normalize ──────────────────────────────────────────

    @app_commands.command(name="replaygain", description="Toggle loudness normalization across all tracks (dynaudnorm)")
    async def replaygain(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_dj(interaction):
            return

        player = self.bot.get_player(interaction.guild_id)
        player.replay_gain = not player.replay_gain
        state = player.replay_gain

        self._restart_audio(interaction.guild_id)

        embed = discord.Embed(
            title       = "🎚️  Replay Gain",
            description = (
                f"Replay Gain (loudness normalization) is now **{'enabled ✅' if state else 'disabled ❌'}**.\n"
                + (
                    "*All tracks will be normalized to a consistent volume via `dynaudnorm` — "
                    "loud tracks get quieter, quiet tracks get louder.*"
                    if state else
                    "*Volume is as encoded in the source audio.*"
                )
            ),
            color       = 0x2ED573 if state else 0x747F8D,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Status: show all A+ settings ─────────────────────────────────────────

    @app_commands.command(name="playbackinfo", description="Show current playback enhancement settings")
    async def playbackinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.bot.get_player(interaction.guild_id)

        def toggle_str(val: bool) -> str:
            return "✅ Enabled" if val else "❌ Disabled"

        embed = discord.Embed(
            title = "🎛️  Playback Settings",
            color = 0x5865F2,
        )
        embed.add_field(name="⚡ Speed",        value=f"`{player.playback_speed}x`",          inline=True)
        embed.add_field(name="🎵 Pitch",        value=f"`{player.pitch_semitones:+d} st`",     inline=True)
        embed.add_field(name="🎚️ Crossfade",   value=f"`{player.crossfade_seconds}s`" if player.crossfade_seconds else "`Off`", inline=True)
        embed.add_field(name="✂️ Silence Trim", value=toggle_str(player.silence_trim),         inline=True)
        embed.add_field(name="📊 Replay Gain",  value=toggle_str(player.replay_gain),           inline=True)
        embed.set_footer(text="Use /speed, /pitch, /crossfade, /silencetrim, /replaygain to change settings.")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(PlaybackCog(bot))
