# -*- coding: utf-8 -*-
"""radio/cog.py — Discord Slash Commands for 24/7 Curated Web Radio & Livestream Directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from models.track import Track
from radio.watchdog import RadioWatchdog
from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot

_STATIONS_FILE = Path(__file__).parent / "stations.json"


def _load_stations() -> dict[str, list[dict[str, str]]]:
    try:
        with open(_STATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class RadioCog(commands.Cog, name="Radio"):
    """24/7 Curated Web Radio & Livestream Directory."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self.stations = _load_stations()
        self.watchdog = RadioWatchdog(bot)
        self.watchdog.start()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild_id:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ This command can only be used in a server.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ This command can only be used in a server.", ephemeral=True
                )
            return False
        return True

    async def cog_unload(self) -> None:
        self.watchdog.stop()

    radio_group = app_commands.Group(
        name="radio",
        description="24/7 Curated Web Radio & Livestream Directory",
    )

    async def _play_radio_station(
        self,
        interaction: discord.Interaction,
        station_id: str,
        custom_url: Optional[str] = None,
        custom_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        if not interaction.guild_id:
            return
        locale = await get_locale(interaction.guild_id, self.bot.db)

        # 1. Resolve station info
        url = custom_url
        name = custom_name or "Custom Radio Stream"
        genre = "Custom"

        if not custom_url:
            matched_station = None
            matched_genre = "Online"
            for g, st_list in self.stations.items():
                for st in st_list:
                    if st["id"] == station_id or st["name"].lower() == station_id.lower():
                        matched_station = st
                        matched_genre = g.title()
                        break
                if matched_station:
                    break

            if not matched_station:
                await interaction.followup.send(
                    embed=error_embed(
                        "Station Not Found",
                        t("radio.station_not_found", locale, station=station_id),
                    ),
                    ephemeral=True,
                )
                return

            url = matched_station["url"]
            name = matched_station["name"]
            genre = matched_genre

        # Validate URL
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            await interaction.followup.send(
                embed=error_embed("Invalid URL", t("radio.invalid_url", locale)),
                ephemeral=True,
            )
            return

        # Ensure user is in voice channel
        music_cog = self.bot.cogs.get("Music")
        if not music_cog:
            await interaction.followup.send("Music system unavailable.", ephemeral=True)
            return

        vc = await music_cog._ensure_voice(interaction)
        if not vc:
            return

        player = self.bot.get_player(interaction.guild_id)
        player.is_radio_mode = True

        # Register 24/7 session
        self.watchdog.register(interaction.guild_id, station_id, name, url)

        # Create live radio track
        track = Track(
            title=f"📻 {name}",
            url=url,
            duration=0,
            uploader=f"{genre} Radio",
            requested_by_id=interaction.user.id,
            requested_by_name=interaction.user.display_name,
            stream_url_cache=url,
        )

        await player.clear()
        await player.enqueue(track)

        # Notify user
        if custom_url:
            embed = success_embed(
                "24/7 Web Radio",
                t("radio.custom_started", locale, url=url),
            )
        else:
            embed = success_embed(
                "24/7 Web Radio",
                t("radio.started", locale, name=name, genre=genre),
            )
        embed.set_footer(text="24/7 Watchdog Active • Auto-reconnect enabled")
        await interaction.followup.send(embed=embed)

        if vc.is_playing() or vc.is_paused():
            player.cancel_prefetch()
            vc.stop()
        elif hasattr(music_cog, "_play_next"):
            await music_cog._play_next(interaction.guild_id)

    @radio_group.command(name="play", description="Tune into a curated 24/7 web radio station")
    @app_commands.describe(
        station="Select or search for a curated radio station",
        genre="Optional filter by genre",
    )
    @app_commands.choices(genre=[
        app_commands.Choice(name="Lofi & Beats", value="lofi"),
        app_commands.Choice(name="Thai Radio & Hits", value="thai"),
        app_commands.Choice(name="SomaFM Underground", value="somafm"),
        app_commands.Choice(name="Jazz & Soul", value="jazz"),
        app_commands.Choice(name="Classical", value="classical"),
    ])
    async def radio_play(
        self,
        interaction: discord.Interaction,
        station: str,
        genre: Optional[str] = None,
    ) -> None:
        await self._play_radio_station(interaction, station_id=station)

    def _filter_station_choices(self, current: str) -> list[app_commands.Choice[str]]:
        choices: list[app_commands.Choice[str]] = []
        cur = current.lower().strip()
        for g_name, st_list in self.stations.items():
            for st in st_list:
                if not cur or cur in st["name"].lower() or cur in st["id"].lower() or cur in g_name.lower():
                    label = f"[{g_name.upper()}] {st['name']}"[:100]
                    choices.append(app_commands.Choice(name=label, value=st["id"]))
                    if len(choices) >= 25:
                        return choices
        return choices

    @radio_play.autocomplete("station")
    async def station_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._filter_station_choices(current)

    @radio_group.command(name="station", description="Alias for /radio play")
    @app_commands.describe(station="Select or search for a curated radio station")
    async def radio_station(
        self,
        interaction: discord.Interaction,
        station: str,
    ) -> None:
        await self._play_radio_station(interaction, station_id=station)

    @radio_station.autocomplete("station")
    async def station_alias_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._filter_station_choices(current)

    @radio_group.command(name="custom", description="Stream a custom 24/7 radio or livestream URL")
    @app_commands.describe(
        stream_url="Direct HTTP/HTTPS audio livestream URL (Icecast, Shoutcast, HLS)",
        name="Optional custom station name",
    )
    async def radio_custom(
        self,
        interaction: discord.Interaction,
        stream_url: str,
        name: Optional[str] = None,
    ) -> None:
        await self._play_radio_station(
            interaction,
            station_id="custom",
            custom_url=stream_url.strip(),
            custom_name=name or "Custom Radio Stream",
        )

    @radio_group.command(name="list", description="Browse all available 24/7 curated radio stations")
    async def radio_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="📻 24/7 Curated Web Radio Directory",
            description="Tune in anytime using `/radio play <station>` or `/radio custom <stream_url>`.",
            color=0x5865F2,
        )
        for g_name, st_list in self.stations.items():
            lines = [f"• **{st['name']}** — *{st['desc']}*" for st in st_list]
            embed.add_field(
                name=f"🎵 {g_name.upper()}",
                value="\n".join(lines),
                inline=False,
            )
        embed.set_footer(text="Uninterrupted 24/7 continuous playback with Watchdog auto-recovery")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "MusicBot") -> None:
    cog = RadioCog(bot)
    setattr(bot, "radio_watchdog", cog.watchdog)
    await bot.add_cog(cog)

