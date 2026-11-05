# -*- coding: utf-8 -*-
"""podcast/views.py — Interactive episode selection view for podcasts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable
import discord

from utils.formatters import truncate, format_duration

if TYPE_CHECKING:
    from podcast.parser import PodcastEpisode, PodcastShow


class PodcastEpisodeSelect(discord.ui.Select):
    """Dropdown menu for selecting an episode from a podcast feed."""

    def __init__(
        self,
        show: "PodcastShow",
        episodes: list["PodcastEpisode"],
        on_select: Callable[[discord.Interaction, "PodcastEpisode"], Awaitable[None]],
    ) -> None:
        self.show = show
        self.episodes = episodes[:25]
        self.on_select = on_select

        options = []
        for i, ep in enumerate(self.episodes):
            dur_str = f" • {format_duration(ep.duration)}" if ep.duration else ""
            date_str = f"{ep.pub_date[:16]}" if ep.pub_date else ""
            desc = f"{date_str}{dur_str}"[:100] or "Podcast Episode"
            options.append(
                discord.SelectOption(
                    label=f"{i + 1}. {truncate(ep.title, 90)}",
                    value=str(i),
                    description=desc,
                    emoji="🎙️",
                )
            )

        super().__init__(
            placeholder="Select a podcast episode to stream...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        idx = int(self.values[0])
        chosen = self.episodes[idx]
        await self.on_select(interaction, chosen)


class PodcastEpisodeSelectView(discord.ui.View):
    """View container for podcast episode selection dropdown."""

    def __init__(
        self,
        show: "PodcastShow",
        episodes: list["PodcastEpisode"],
        on_select: Callable[[discord.Interaction, "PodcastEpisode"], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(PodcastEpisodeSelect(show, episodes, on_select))

