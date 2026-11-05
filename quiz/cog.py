# -*- coding: utf-8 -*-
"""quiz/cog.py — Discord Slash Commands for Blind Music Quiz & Leaderboard."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.i18n import get_locale, t
from quiz.engine import QuizSession
from quiz.views import QuizQuestionView
from utils.embeds import error_embed, info_embed, success_embed

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


class QuizCog(commands.Cog, name="Quiz"):
    """Interactive Blind Music Quiz minigame powered by MySQL B-Tree sampling."""

    def __init__(self, bot: "MusicBot") -> None:
        self.bot = bot
        self._active_sessions: dict[int, QuizSession] = {}

    quiz_group = app_commands.Group(name="quiz", description="Interactive Blind Music Quiz game")

    @quiz_group.command(name="start", description="Start an interactive blind music quiz session")
    @app_commands.describe(
        rounds="Number of quiz rounds (1-10, default: 5)",
        source="Catalog source for random song selection (history, favorites, playlist)",
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="📜 Server Play History (Instant B-Tree Index)", value="history"),
        app_commands.Choice(name="❤️ User Favorites", value="favorites"),
        app_commands.Choice(name="📁 User Playlists", value="playlist"),
    ])
    async def start(
        self,
        interaction: discord.Interaction,
        rounds: int = 5,
        source: str = "history",
    ) -> None:
        await interaction.response.defer()
        guild_id = interaction.guild_id
        if not guild_id:
            return

        locale = await get_locale(guild_id, self.bot.db)

        if guild_id in self._active_sessions and self._active_sessions[guild_id].is_active:
            await interaction.followup.send(
                embed=error_embed("Quiz in Progress", "A music quiz is already active in this server! Finish it first."),
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.followup.send(
                embed=error_embed(t("title.not_in_voice", locale), t("error.not_in_voice", locale)),
                ephemeral=True,
            )
            return

        channel = member.voice.channel
        guild = interaction.guild
        vc = guild.voice_client if guild else None

        if not vc:
            try:
                vc = await channel.connect(self_deaf=True)
            except Exception as e:
                await interaction.followup.send(
                    embed=error_embed("Voice Connection Failed", str(e)), ephemeral=True
                )
                return
        elif vc.channel != channel:
            await interaction.followup.send(
                embed=error_embed(t("title.wrong_channel", locale), t("error.wrong_channel", locale, channel=vc.channel.name)),
                ephemeral=True,
            )
            return

        # Initialize session
        session = QuizSession(
            bot=self.bot,
            guild_id=guild_id,
            channel=interaction.channel,  # type: ignore
            total_rounds=rounds,
            source=source,
        )
        self._active_sessions[guild_id] = session
        session.is_active = True

        start_embed = discord.Embed(
            title="🎮 Blind Music Quiz Starting!",
            description=(
                f"**Rounds:** `{session.total_rounds}`\n"
                f"**Source:** `{source.capitalize()}`\n\n"
                "Listen closely to the audio snippet and select the correct title from the 4 buttons, "
                "or type your guess directly in this channel!\n\n"
                "🎯 *First correct answer receives bonus points!*"
            ),
            color=0x5865F2,
        )
        start_embed.set_footer(text="Powered by MySQL 8.0+ B-Tree Sampling • Music Bot Gen 5.0")
        await interaction.followup.send(embed=start_embed)

        # Run game loop as background task
        self.bot.track_task(self._run_quiz_game(session, vc, locale))

    async def _run_quiz_game(self, session: QuizSession, vc: discord.VoiceClient, locale: str) -> None:
        guild_id = session.guild_id
        try:
            for round_num in range(1, session.total_rounds + 1):
                round_data = await session.prepare_round()
                if not round_data:
                    await session.channel.send(
                        embed=error_embed("Quiz Ended", t("quiz.empty_source", locale))
                    )
                    break

                track = round_data["track"]
                options = round_data["options"]
                correct_idx = round_data["correct_index"]

                # Resolve audio URL
                try:
                    stream_url = await self.bot.youtube.get_stream_url(track.url, track)
                except Exception:
                    stream_url = None

                if not stream_url:
                    continue

                # Play snippet for 20 seconds
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

                ffmpeg_opts = self.bot.audio_processor.build_ffmpeg_options(volume=0.8)
                source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
                vc.play(source)

                # Send Question View
                view = QuizQuestionView(session, options)
                q_embed = discord.Embed(
                    title=f"🎵 Round {round_num} of {session.total_rounds}: Guess the Song!",
                    description="Listen to the snippet! Click the correct button or type your guess in chat.\n⏳ You have **20 seconds**!",
                    color=0x1DB954,
                )
                q_msg = await session.channel.send(embed=q_embed, view=view)

                # Listen for chat guesses concurrently
                def check_msg(m: discord.Message) -> bool:
                    return m.channel.id == session.channel.id and not m.author.bot

                end_time = asyncio.get_event_loop().time() + 20.0
                while asyncio.get_event_loop().time() < end_time and session.round_active:
                    timeout = max(0.5, end_time - asyncio.get_event_loop().time())
                    try:
                        msg = await self.bot.wait_for("message", check=check_msg, timeout=timeout)
                        if session.register_text_guess(msg.author, msg.content):
                            await session.channel.send(
                                f"🎯 **{msg.author.display_name}** guessed it correctly in chat: **{track.title}**! (+15 pts)"
                            )
                            break
                    except asyncio.TimeoutError:
                        break

                session.round_active = False
                view.stop()
                if vc.is_playing():
                    vc.stop()

                # Announce round conclusion
                correct_title = options[correct_idx]
                r_embed = discord.Embed(
                    title=f"🔔 Round {round_num} Concluded!",
                    description=f"The song was: **{correct_title}**\n[Listen to Track]({track.url})",
                    color=0xF1C40F,
                )
                if track.thumbnail:
                    r_embed.set_thumbnail(url=track.thumbnail)

                if session.scores:
                    top_players = sorted(session.scores.items(), key=lambda x: x[1], reverse=True)[:5]
                    standings = "\n".join(
                        f"`{i+1}.` {session.user_names.get(uid, f'User {uid}')} — **{pts} pts**"
                        for i, (uid, pts) in enumerate(top_players)
                    )
                    r_embed.add_field(name="🏆 Current Standings", value=standings, inline=False)

                await session.channel.send(embed=r_embed)
                await asyncio.sleep(3.0)

            # Final Game Results & MySQL Leaderboard Recording
            final_embed = discord.Embed(
                title="🏁 Music Quiz Finished!",
                color=0x2ED573,
            )
            if session.scores:
                sorted_scores = sorted(session.scores.items(), key=lambda x: x[1], reverse=True)
                winner_id, winner_pts = sorted_scores[0]
                winner_name = session.user_names.get(winner_id, f"User {winner_id}")
                final_embed.description = f"🥇 Winner: **{winner_name}** with **{winner_pts} points**!\n\nAll scores have been recorded to the server Leaderboard."

                # Record scores into MySQL database
                for uid, pts in session.scores.items():
                    is_winner = (uid == winner_id)
                    await self.bot.db.record_quiz_score(guild_id, uid, pts, won=is_winner)
            else:
                final_embed.description = "No points were scored this session. Try again soon!"

            await session.channel.send(embed=final_embed)

        except Exception as exc:
            logger.error("Quiz session error: %s", exc)
        finally:
            session.is_active = False
            self._active_sessions.pop(guild_id, None)

    @quiz_group.command(name="leaderboard", description="Display the guild's Music Quiz Leaderboard")
    @app_commands.describe(limit="Number of top players to display (default: 10)")
    async def leaderboard(self, interaction: discord.Interaction, limit: int = 10) -> None:
        await interaction.response.defer()
        guild_id = interaction.guild_id
        if not guild_id:
            return

        rows = await self.bot.db.get_quiz_leaderboard(guild_id, limit=max(1, min(25, limit)))
        if not rows:
            embed = info_embed(
                "Quiz Leaderboard",
                "No quiz scores recorded yet in this server! Start a game with `/quiz start`."
            )
            await interaction.followup.send(embed=embed)
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(rows):
            rank = medals[idx] if idx < 3 else f"`{idx+1}.`"
            user_id = row["user_id"]
            member = interaction.guild.get_member(user_id) if interaction.guild else None
            name = member.display_name if member else f"User {user_id}"
            lines.append(
                f"{rank} **{name}** — `{row['score']} pts` (Won: `{row['games_won']}`, Correct: `{row['correct_answers']}`)"
            )

        embed = discord.Embed(
            title="🏆 Music Quiz Hall of Fame",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text="MySQL 8.0+ Persistent Leaderboard • Music Bot Gen 5.0")
        await interaction.followup.send(embed=embed)


async def setup(bot: "MusicBot") -> None:
    await bot.add_cog(QuizCog(bot))

