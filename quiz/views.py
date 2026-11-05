# -*- coding: utf-8 -*-
"""quiz/views.py — Discord UI Views and interactive buttons for Music Quiz."""

from __future__ import annotations

from typing import TYPE_CHECKING
import discord

from utils.formatters import truncate

if TYPE_CHECKING:
    from quiz.engine import QuizSession


class QuizOptionButton(discord.ui.Button):
    def __init__(self, index: int, label_text: str) -> None:
        prefix = ["A", "B", "C", "D"][index]
        super().__init__(
            label=f"{prefix}. {truncate(label_text, 70)}",
            style=discord.ButtonStyle.primary,
            custom_id=f"quiz_opt_{index}",
            row=index // 2,
        )
        self.option_index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: QuizQuestionView = self.view  # type: ignore
        session = view.session

        if not session.round_active:
            await interaction.response.send_message("⏳ This round has already ended!", ephemeral=True)
            return

        if interaction.user.id in session.answered_users:
            await interaction.response.send_message("⚠️ You have already submitted an answer for this round!", ephemeral=True)
            return

        is_correct = session.register_answer(interaction.user, self.option_index)

        if is_correct:
            await interaction.response.send_message(
                f"🎉 **Correct answer, {interaction.user.display_name}!** (+Points)",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ **Incorrect answer, {interaction.user.display_name}!** Better luck on the next song.",
                ephemeral=True,
            )


class QuizQuestionView(discord.ui.View):
    def __init__(self, session: "QuizSession", options: list[str]) -> None:
        super().__init__(timeout=25)
        self.session = session
        for i, opt in enumerate(options[:4]):
            self.add_item(QuizOptionButton(i, opt))

    async def on_timeout(self) -> None:
        self.session.round_active = False
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

