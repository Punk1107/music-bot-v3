# -*- coding: utf-8 -*-
"""quiz/engine.py — Interactive Blind Music Quiz Game Engine (Gen 5.0)."""

from __future__ import annotations

import asyncio
import difflib
import logging
import random
import re
from typing import TYPE_CHECKING, Optional

from models.track import Track

if TYPE_CHECKING:
    import discord
    from main import MusicBot

logger = logging.getLogger(__name__)


def clean_song_title(title: str) -> str:
    """Strip common metadata tags like '(Official Video)', '[MV]', etc."""
    cleaned = re.sub(r"\(.*?(?:official|video|audio|lyrics|mv|hd|4k|feat).*?\)", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[.*?(?:official|video|audio|lyrics|mv|hd|4k|feat).*?\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or title


def is_answer_correct(guess: str, actual_title: str, threshold: float = 0.75) -> bool:
    """
    Check if a guess is correct using direct containment or difflib fuzzy similarity.
    """
    g = guess.lower().strip()
    act = actual_title.lower().strip()
    clean_act = clean_song_title(actual_title).lower().strip()

    if not g:
        return False

    if g == act or g == clean_act:
        return True

    # If the clean title is contained inside guess or vice versa (for multi-word titles)
    if len(clean_act) >= 4 and (clean_act in g or (len(g) >= 4 and g in clean_act)):
        return True

    # Fuzzy match using SequenceMatcher
    ratio1 = difflib.SequenceMatcher(None, g, act).ratio()
    ratio2 = difflib.SequenceMatcher(None, g, clean_act).ratio()
    return max(ratio1, ratio2) >= threshold


class QuizSession:
    """State manager for an active Blind Music Quiz in a guild."""

    def __init__(
        self,
        bot: "MusicBot",
        guild_id: int,
        channel: discord.TextChannel,
        total_rounds: int = 5,
        source: str = "history",
    ) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.channel = channel
        self.total_rounds = max(1, min(20, int(total_rounds)))
        self.source = source
        self.current_round: int = 0
        self.scores: dict[int, int] = {}  # user_id -> points
        self.user_names: dict[int, str] = {}  # user_id -> name
        self.is_active: bool = False
        self.round_active: bool = False
        self.current_track: Optional[Track] = None
        self.current_options: list[str] = []
        self.correct_index: int = 0
        self.round_winner_id: Optional[int] = None
        self.answered_users: set[int] = set()

    async def prepare_round(self) -> Optional[dict]:
        """Fetch a random track and 3 distractors for the next round."""
        track = await self.bot.db.get_random_quiz_track(self.guild_id, source=self.source)
        if not track:
            return None

        self.current_track = track
        self.round_active = True
        self.round_winner_id = None
        self.answered_users.clear()
        self.current_round += 1

        clean_correct = clean_song_title(track.title)
        distractors = await self.bot.db.get_quiz_distractors(
            self.guild_id,
            exclude_title=clean_correct,
            limit=3,
            source=self.source,
        )

        options = [clean_correct] + [clean_song_title(d) for d in distractors]
        random.shuffle(options)
        self.current_options = options
        self.correct_index = options.index(clean_correct)

        return {
            "track": track,
            "options": options,
            "correct_index": self.correct_index,
            "round": self.current_round,
            "total_rounds": self.total_rounds,
        }

    def register_answer(self, user: discord.User, option_index: int) -> bool:
        """Register a button click answer."""
        if not self.round_active or user.id in self.answered_users:
            return False

        self.answered_users.add(user.id)
        self.user_names[user.id] = user.display_name

        if option_index == self.correct_index:
            points = 10
            # First to answer gets bonus
            if self.round_winner_id is None:
                self.round_winner_id = user.id
                points += 5
            self.scores[user.id] = self.scores.get(user.id, 0) + points
            return True

        return False

    def register_text_guess(self, user: discord.User, guess: str) -> bool:
        """Register a chat text guess."""
        if not self.round_active or not self.current_track:
            return False

        if is_answer_correct(guess, self.current_track.title):
            self.user_names[user.id] = user.display_name
            points = 15
            if self.round_winner_id is None:
                self.round_winner_id = user.id
            self.scores[user.id] = self.scores.get(user.id, 0) + points
            self.round_active = False  # Text correct ends round immediately
            return True

        return False

