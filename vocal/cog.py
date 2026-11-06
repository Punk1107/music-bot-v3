# -*- coding: utf-8 -*-
"""vocal/cog.py — Discord slash commands for Mid-Side Vocal Remover & Acapella Isolator."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from utils.embeds import error_embed, success_embed
from utils.error_handler import dj_required_embed
from vocal.filter import build_vocal_filter

if TYPE_CHECKING:
    from main import MusicBot


class VocalCog(commands.Cog, name="Vocal"):
    """Mid-Side Stereo Vocal Remover & Acapella Isolator (Karaoke 2.0)."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Verify the user is connected to voice and has DJ/Admin permissions."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False

        locale = await get_locale(interaction.guild_id, self.bot.db)
        if not member.voice or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.not_in_voice", locale), t("error.not_in_voice", locale)),
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        vc = guild.voice_client if guild else None
        if vc and vc.channel != member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.wrong_channel", locale), t("error.wrong_channel", locale, channel=vc.channel.name)),
                ephemeral=True,
            )
            return False

        cfg = await self.bot.db.get_server_config(interaction.guild_id)
        if not cfg.dj_role_id:
            return True
        if member.guild_permissions.administrator:
            return True
        if any(r.id == cfg.dj_role_id for r in member.roles):
            return True

        await interaction.followup.send(embed=dj_required_embed(interaction), ephemeral=True)
        return False

    def _restart_audio(self, guild_id: int) -> None:
        """Hot-reload current playback to apply new vocal filter seamlessly."""
        if hasattr(self.bot, "seek") and self.bot.seek:
            if hasattr(self.bot, "track_task"):
                self.bot.track_task(self.bot.seek.hot_reload(guild_id), name=f"vocal_hot_reload_{guild_id}")
            else:
                asyncio.create_task(self.bot.seek.hot_reload(guild_id))
        else:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

    @app_commands.command(
        name="vocal",
        description="Mid-Side stereo vocal remover (Karaoke 2.0) & acapella isolator",
    )
    @app_commands.describe(
        mode="Select vocal processing mode (remove, isolate, off)",
        suppression="Suppression intensity 1-100% for vocal removal (default: 100)",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="🎤 Remove Vocals (Karaoke 2.0)", value="remove"),
        app_commands.Choice(name="🎙️ Isolate Vocals (Acapella)", value="isolate"),
        app_commands.Choice(name="❌ Off (Disable)", value="off"),
    ])
    async def vocal(
        self,
        interaction: discord.Interaction,
        mode: Optional[str] = None,
        suppression: Optional[app_commands.Range[int, 1, 100]] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._check_permissions(interaction):
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        if mode is None and suppression is None:
            # Toggle off if active, otherwise activate remove mode
            mode = "off" if player.vocal_mode else "remove"
        elif mode is None and suppression is not None and not player.vocal_mode:
            mode = "remove"

        if suppression is not None:
            if not 1 <= int(suppression) <= 100:
                await interaction.followup.send(
                    embed=error_embed("Invalid Suppression", t("vocal.invalid_suppression", locale)),
                    ephemeral=True,
                )
                return
            player.vocal_suppression = int(suppression)

        if mode is not None:
            norm_mode = mode.lower().strip()
            if norm_mode == "off":
                player.vocal_mode = None
            else:
                player.vocal_mode = norm_mode

        self._restart_audio(interaction.guild_id)

        if not player.vocal_mode:
            embed = discord.Embed(
                title="🎤 Vocal Processing",
                description=t("vocal.off", locale),
                color=0x747F8D,
            )
        else:
            mode_desc = t(
                "vocal.mode_set",
                locale,
                mode=player.vocal_mode.capitalize(),
                suppression=player.vocal_suppression,
            )
            embed = discord.Embed(
                title="🎤 Vocal Processing (Studio DSP)",
                description=mode_desc,
                color=0x2ED573,
            )
            embed.set_footer(
                text="Pure FFmpeg Mid-Side DSP • Best results with center-panned vocals"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(VocalCog(bot))

