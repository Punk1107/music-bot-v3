# -*- coding: utf-8 -*-
"""podcast/cog.py — Discord Slash Commands for Native Podcast & Audio RSS XML Parser."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from models.track import Track
from podcast.parser import fetch_and_parse_podcast, PodcastEpisode
from podcast.views import PodcastEpisodeSelectView
from utils.embeds import error_embed, info_embed, success_embed
from utils.formatters import truncate, format_duration

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class PodcastCog(commands.Cog, name="Podcast"):
    """Native Podcast & Audio RSS XML Parser."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot

    podcast_group = app_commands.Group(
        name="podcast",
        description="Native Podcast & Audio RSS XML Parser",
    )

    async def _stream_episode(
        self,
        interaction: discord.Interaction,
        episode: PodcastEpisode,
        show_title: str,
        image_url: Optional[str] = None,
    ) -> None:
        music_cog = self.bot.cogs.get("Music")
        if not music_cog:
            await interaction.followup.send("Music system unavailable.", ephemeral=True)
            return

        vc = await music_cog._ensure_voice(interaction)
        if not vc:
            return

        locale = await get_locale(interaction.guild_id, self.bot.db)
        player = self.bot.get_player(interaction.guild_id)

        # Check per-user queue limit
        if player.per_user_limit > 0 and player.user_track_count(interaction.user.id) >= player.per_user_limit:
            await interaction.followup.send(
                embed=error_embed("Queue Limit", t("queue.user_limit_exceeded", locale, limit=player.per_user_limit)),
                ephemeral=True,
            )
            return

        track = Track(
            title=f"🎙️ {episode.title}",
            url=episode.audio_url,
            duration=episode.duration,
            thumbnail=image_url,
            uploader=show_title,
            requested_by_id=interaction.user.id,
            requested_by_name=interaction.user.display_name,
            stream_url_cache=episode.audio_url,  # Direct MP3 stream bypass
        )

        await player.enqueue(track)
        if player.queue_mode == "fair":
            await player.reorder_fair_share()
        if vc and vc.channel and hasattr(self.bot, "db") and hasattr(self.bot.db, "save_queue"):
            self.bot.track_task(
                self.bot.db.save_queue(interaction.guild_id, vc.channel.id, player.queue)
            )

        embed = success_embed(
            "Podcast Enqueued",
            t("podcast.loaded", locale, title=episode.title, show=show_title),
        )
        if image_url:
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text=f"Duration: {format_duration(episode.duration)} • Pure XML Parser")

        await interaction.followup.send(embed=embed)

        if not vc.is_playing() and not vc.is_paused():
            await music_cog._play_next(interaction.guild_id)
        else:
            if hasattr(self.bot, "update_clean_board"):
                self.bot.track_task(self.bot.update_clean_board(interaction.guild_id))

    @podcast_group.command(name="play", description="Play an episode from a podcast RSS feed URL")
    @app_commands.describe(
        rss_url="Direct podcast RSS XML feed URL",
        episode="Episode number (1 = latest) or episode title keyword",
    )
    async def podcast_play(
        self,
        interaction: discord.Interaction,
        rss_url: str,
        episode: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        locale = await get_locale(interaction.guild_id, self.bot.db)

        clean_url = rss_url.strip()
        if not clean_url.startswith(("http://", "https://")):
            await interaction.followup.send(
                embed=error_embed("Invalid URL", t("podcast.parse_error", locale)),
                ephemeral=True,
            )
            return

        show = await fetch_and_parse_podcast(self.bot.http_session, clean_url)
        if not show:
            await interaction.followup.send(
                embed=error_embed("RSS Parse Failed", t("podcast.parse_error", locale)),
                ephemeral=True,
            )
            return

        if not show.episodes:
            await interaction.followup.send(
                embed=error_embed("No Episodes", t("podcast.no_episodes", locale)),
                ephemeral=True,
            )
            return

        target_ep: Optional[PodcastEpisode] = None

        if episode:
            # Check if integer index (1-based: 1 = latest)
            try:
                idx = int(episode) - 1
                if 0 <= idx < len(show.episodes):
                    target_ep = show.episodes[idx]
            except ValueError:
                # Fuzzy keyword match
                ep_lower = episode.lower()
                for ep in show.episodes:
                    if ep_lower in ep.title.lower():
                        target_ep = ep
                        break

        # Default to latest episode if no match or not provided
        if not target_ep:
            target_ep = show.episodes[0]

        await self._stream_episode(interaction, target_ep, show.title, show.image_url)

    @podcast_group.command(name="episodes", description="Browse episodes of a podcast RSS feed")
    @app_commands.describe(rss_url="Direct podcast RSS XML feed URL")
    async def podcast_episodes(
        self,
        interaction: discord.Interaction,
        rss_url: str,
    ) -> None:
        await interaction.response.defer()
        locale = await get_locale(interaction.guild_id, self.bot.db)

        clean_url = rss_url.strip()
        if not clean_url.startswith(("http://", "https://")):
            await interaction.followup.send(
                embed=error_embed("Invalid URL", t("podcast.parse_error", locale)),
                ephemeral=True,
            )
            return

        show = await fetch_and_parse_podcast(self.bot.http_session, clean_url)
        if not show:
            await interaction.followup.send(
                embed=error_embed("RSS Parse Failed", t("podcast.parse_error", locale)),
                ephemeral=True,
            )
            return

        if not show.episodes:
            await interaction.followup.send(
                embed=error_embed("No Episodes", t("podcast.no_episodes", locale)),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎙️ {show.title}",
            description=truncate(show.description, 300) + f"\n\n**Found {len(show.episodes)} episodes.** Pick an episode below to start playing:",
            color=0x9B59B6,
        )
        if show.image_url:
            embed.set_thumbnail(url=show.image_url)

        async def on_episode_selected(inter: discord.Interaction, chosen_ep: PodcastEpisode) -> None:
            await inter.response.defer()
            await self._stream_episode(inter, chosen_ep, show.title, show.image_url)

        view = PodcastEpisodeSelectView(show, show.episodes, on_episode_selected)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(PodcastCog(bot))

