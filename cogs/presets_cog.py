# -*- coding: utf-8 -*-
"""
cogs/presets_cog.py — Guild Presets (Feature 26) for Music Bot V3.

A preset bundles: effects, volume, quality, loop_mode, speed, pitch, crossfade,
silence_trim, replay_gain — applied atomically to the guild player.

Built-in presets: Gaming, Study, Anime, Chill
Custom presets: DJ/Admin can save and load guild-specific presets.

Commands:
  /preset load <name>   — Apply a built-in or saved preset
  /preset save <name>   — Save current settings as a named preset
  /preset list          — Show all available presets
  /preset delete <name> — Delete a saved custom preset (Admin only)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from models.enums import AudioEffect, AudioQuality, LoopMode
from utils.embeds import error_embed, success_embed, info_embed
from core.i18n import get_locale, t

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# ── Built-in Preset Definitions ───────────────────────────────────────────────

_BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "gaming": {
        "_label":       "🎮 Gaming",
        "effects":      [AudioEffect.BASS_BOOST.value, AudioEffect.AUDIO_8D.value],
        "volume":       0.80,
        "quality":      AudioQuality.HIGH.value,
        "loop":         LoopMode.QUEUE.value,
        "speed":        1.0,
        "pitch":        0,
        "crossfade":    0,
        "silence_trim": False,
        "replay_gain":  False,
        "_desc":        "Heavy bass boost + 8D audio — perfect for gaming sessions.",
    },
    "study": {
        "_label":       "📚 Study",
        "effects":      [AudioEffect.COMPRESSOR.value, AudioEffect.LIMITER.value],
        "volume":       0.50,
        "quality":      AudioQuality.MEDIUM.value,
        "loop":         LoopMode.OFF.value,
        "speed":        1.0,
        "pitch":        0,
        "crossfade":    5,
        "silence_trim": True,
        "replay_gain":  True,
        "_desc":        "Compressed, normalized, soft crossfades — great for study playlists.",
    },
    "anime": {
        "_label":       "🌸 Anime",
        "effects":      [AudioEffect.NIGHTCORE.value],
        "volume":       1.00,
        "quality":      AudioQuality.HIGH.value,
        "loop":         LoopMode.OFF.value,
        "speed":        1.0,
        "pitch":        1,
        "crossfade":    3,
        "silence_trim": False,
        "replay_gain":  False,
        "_desc":        "Nightcore effect + slight pitch-up — anime vibes only.",
    },
    "chill": {
        "_label":       "🌙 Chill",
        "effects":      [AudioEffect.REVERB.value, AudioEffect.CHORUS.value],
        "volume":       0.70,
        "quality":      AudioQuality.HIGH.value,
        "loop":         LoopMode.OFF.value,
        "speed":        0.75,
        "pitch":        -1,
        "crossfade":    8,
        "silence_trim": False,
        "replay_gain":  True,
        "_desc":        "Reverb + Chorus, slowed 0.75x, long crossfades — maximum chill.",
    },
}


def _apply_preset(player, preset: dict[str, Any]) -> None:
    """Apply a preset dict to a GuildPlayer instance."""
    from models.enums import AudioEffect, AudioQuality, LoopMode

    # Effects
    raw_effects = preset.get("effects", [])
    player.effects = []
    for e in raw_effects:
        try:
            player.effects.append(AudioEffect(e))
        except ValueError:
            pass

    # Volume / quality / loop
    player.volume = float(preset.get("volume", 1.0))

    # Quality lives in server config, not player — handled separately
    # Loop mode
    try:
        player.loop_mode = LoopMode(preset.get("loop", "off"))
    except ValueError:
        player.loop_mode = LoopMode.OFF

    # A+ controls
    player.playback_speed    = float(preset.get("speed",        1.0))
    player.pitch_semitones   = int(preset.get("pitch",          0))
    player.crossfade_seconds = int(preset.get("crossfade",      0))
    player.silence_trim      = bool(preset.get("silence_trim",  False))
    player.replay_gain       = bool(preset.get("replay_gain",   False))


def _player_to_preset(player, quality_value: str) -> dict[str, Any]:
    """Serialize current player state to a preset dict."""
    return {
        "effects":      [e.value for e in player.effects],
        "volume":       player.volume,
        "quality":      quality_value,
        "loop":         player.loop_mode.value,
        "speed":        player.playback_speed,
        "pitch":        player.pitch_semitones,
        "crossfade":    player.crossfade_seconds,
        "silence_trim": player.silence_trim,
        "replay_gain":  player.replay_gain,
    }


def _preset_embed(name: str, preset: dict, is_active: bool = False) -> discord.Embed:
    label   = preset.get("_label", name.title())
    desc    = preset.get("_desc", "Custom preset")
    effects = ", ".join(
        AudioEffect(e).display_name() for e in preset.get("effects", [])
        if e in [ae.value for ae in AudioEffect]
    ) or "None"

    color = 0x5865F2 if is_active else 0x70A1FF
    embed = discord.Embed(
        title       = f"{'▶ ' if is_active else ''}{label}",
        description = desc,
        color       = color,
    )
    embed.add_field(name="🎛 Effects",       value=effects,                                           inline=False)
    embed.add_field(name="🔊 Volume",        value=f"{int(preset.get('volume', 1.0) * 100)}%",        inline=True)
    embed.add_field(name="⚡ Speed",         value=f"{preset.get('speed', 1.0)}x",                   inline=True)
    embed.add_field(name="🎵 Pitch",         value=f"{preset.get('pitch', 0):+d} st",                 inline=True)
    embed.add_field(name="🎚 Crossfade",     value=f"{preset.get('crossfade', 0)}s" if preset.get("crossfade") else "Off", inline=True)
    embed.add_field(name="✂ Silence Trim",  value="✅" if preset.get("silence_trim") else "❌",      inline=True)
    embed.add_field(name="📊 Replay Gain",   value="✅" if preset.get("replay_gain") else "❌",       inline=True)
    embed.add_field(name="🔁 Loop",          value=preset.get("loop", "off").title(),                 inline=True)
    return embed


# ── Cog ──────────────────────────────────────────────────────────────────────

class PresetsCog(commands.Cog, name="Presets"):
    """Guild Preset commands."""

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
        await interaction.followup.send(embed=dj_required_embed(interaction), ephemeral=True)
        return False

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    def _restart_audio(self, guild_id: int) -> None:
        import asyncio
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"presets_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            vc = guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()

    def _all_presets(self, guild_presets: dict) -> dict[str, dict]:
        """Merge built-ins with guild customs (guild presets override built-ins of same name)."""
        merged = dict(_BUILTIN_PRESETS)
        merged.update(guild_presets)
        return merged

    # ── Slash Command Group: /preset ──────────────────────────────────────────

    preset_group = app_commands.Group(
        name="preset",
        description="Manage and apply guild presets",
    )

    @preset_group.command(name="list", description="Show all available presets")
    async def pr_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        all_p = self._all_presets(cfg.guild_presets)
        lines = []
        for k, p in all_p.items():
            label  = p.get("_label", k.title())
            source = "🏷 Built-in" if k in _BUILTIN_PRESETS else "💾 Custom"
            lines.append(f"**{label}** (`{k}`)  {source}")

        embed = discord.Embed(
            title       = t("preset.list_title", locale),
            description = "\n".join(lines) if lines else "No presets found.",
            color       = 0x5865F2,
        )
        embed.set_footer(text="Use /preset load <name> to apply a preset")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @preset_group.command(name="load", description="Apply a preset to the bot")
    @app_commands.describe(name="Preset name to apply")
    async def pr_load(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not await self._check_dj(interaction):
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        name_lower = name.strip().lower()
        if not name_lower:
            await interaction.followup.send(
                embed=error_embed("Missing Name", t("preset.name_required", locale)),
                ephemeral=True,
            )
            return

        all_p  = self._all_presets(cfg.guild_presets)
        preset = all_p.get(name_lower)
        if not preset:
            available = ", ".join(f"`{k}`" for k in all_p)
            await interaction.followup.send(
                embed=error_embed(
                    "Preset Not Found",
                    f"{t('preset.not_found', locale, name=name_lower)}\nAvailable: {available}",
                ),
                ephemeral=True,
            )
            return

        player = self.bot.get_player(interaction.guild_id)
        _apply_preset(player, preset)

        quality_val = preset.get("quality", "high")
        try:
            cfg.audio_quality = AudioQuality(quality_val)
            await self.bot.db.save_server_config(cfg)
        except Exception:
            pass

        self._restart_audio(interaction.guild_id)
        embed = _preset_embed(name_lower, preset, is_active=True)
        embed.title = t("preset.applied", locale, name=preset.get('_label', name_lower.title()))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @preset_group.command(name="save", description="Save current settings as a guild preset")
    @app_commands.describe(name="Preset name (alphanumeric, max 30 chars)")
    async def pr_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not await self._check_dj(interaction):
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        name_lower = name.strip().lower()
        if not name_lower:
            await interaction.followup.send(
                embed=error_embed("Missing Name", t("preset.name_required", locale)),
                ephemeral=True,
            )
            return
        if name_lower in _BUILTIN_PRESETS:
            await interaction.followup.send(
                embed=error_embed(
                    "Reserved Name",
                    t("preset.reserved_name", locale, name=name_lower),
                ),
                ephemeral=True,
            )
            return

        player      = self.bot.get_player(interaction.guild_id)
        preset_data = _player_to_preset(player, cfg.audio_quality.value)
        preset_data["_label"] = name_lower.title()
        preset_data["_desc"]  = f"Custom preset saved by {interaction.user.display_name}"

        cfg.guild_presets[name_lower] = preset_data
        await self.bot.db.save_server_config(cfg)

        embed = _preset_embed(name_lower, preset_data)
        embed.title = t("preset.saved", locale, name=name_lower)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @preset_group.command(name="delete", description="Remove a custom guild preset (Admin only)")
    @app_commands.describe(name="Preset name to delete")
    async def pr_delete(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not self._is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Permission Denied", t("preset.admin_required", locale)),
                ephemeral=True,
            )
            return

        name_lower = name.strip().lower()
        if name_lower in _BUILTIN_PRESETS:
            await interaction.followup.send(
                embed=error_embed("Cannot Delete", t("preset.cannot_delete", locale)),
                ephemeral=True,
            )
            return

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if name_lower not in cfg.guild_presets:
            await interaction.followup.send(
                embed=error_embed("Not Found", t("preset.not_found", locale, name=name_lower)),
                ephemeral=True,
            )
            return

        del cfg.guild_presets[name_lower]
        await self.bot.db.save_server_config(cfg)
        await interaction.followup.send(
            embed=success_embed("Preset Deleted", t("preset.deleted", locale, name=name_lower)),
            ephemeral=True,
        )

    @pr_load.autocomplete("name")
    @pr_delete.autocomplete("name")
    async def preset_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice]:
        try:
            cfg = await self.bot.db.get_server_config(interaction.guild_id)
        except Exception:
            cfg = None

        guild_presets = cfg.guild_presets if cfg else {}
        all_p = self._all_presets(guild_presets)
        return [
            app_commands.Choice(
                name=p.get("_label", k.title())[:100],
                value=k,
            )
            for k, p in all_p.items()
            if current.lower() in k.lower() or current.lower() in p.get("_label", "").lower()
        ][:25]


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(PresetsCog(bot))
