# -*- coding: utf-8 -*-
"""
tests/test_slash_commands.py — Unit tests for slash command parity and /help command UX/UI.

Covers:
  1. Complete CommandTree parity with README.md (all 9 domains, 50+ commands)
  2. Subcommand structure verification for Group commands (favorite, bookmark, preset, equalizer, djset, requestchannel)
  3. /voteskip command execution and democratic voice voting flow
  4. /help command embed builder (overview and individual categories, char limits <= 6000, field values <= 1024)
  5. HelpCategorySelect and HelpNavigationSelectView button callbacks and interactive navigation
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord
from discord.ext import commands

import main
from cogs.info import (
    HELP_CATEGORIES,
    _CAT_KEYS,
    build_help_embed,
    HelpCategorySelect,
    HelpNavigationSelectView,
    InfoCog,
)
from cogs.queue_cog import QueueCog
from models.track import Track


# ── 1. Command Tree Parity with README.md ────────────────────────────────────

EXPECTED_TOP_LEVEL_COMMANDS = {
    # 1. Core Playback
    "play", "playnext", "search", "pause", "resume", "skip", "stop", "nowplaying", "join", "leave",
    # 2. Democratic Voice Voting
    "voteskip", "voteshuffle", "voteclear",
    # 3. Queue Operations & Control
    "queue", "shuffle", "clear", "loop", "remove", "move", "jump", "undo", "qsearch", "qhistory",
    "queuelock", "queueperm", "duplicates", "qexport", "qimport",
    # 4. Seeking & Looping
    "seek", "forward", "rewind", "replay", "restart", "loopab", "loopab_off",
    # 5. Chapters, Lyrics & Recommendations
    "chapters", "chapter_jump", "cjump", "lyrics", "autoplay",
    # 6. Multi-Source Search
    "scsearch",
    # 7. Audio DSP, Equalizer & Soundstage
    "volume", "effects", "effects_list", "effects_clear", "quality", "equalizer", "eq",
    "loudnorm", "pan", "stereowide", "speed", "pitch", "crossfade", "silencetrim", "replaygain", "playbackinfo",
    # 8. Customization, Favorites & Presets
    "favorite", "bookmark", "preset", "theme", "themeinfo", "sleep", "sleepstatus", "language", "languageinfo",
    # 9. Administration & Diagnostics
    "djset", "requestchannel", "autoplaylist", "idletimeout", "history", "stats", "botstats", "health", "cacheinfo", "help",
    # Local Web Dashboard & Tier Analytics
    "dashboard", "analytics",
}


@pytest.fixture
def registered_bot():
    """Build a test bot instance with all main._COGS loaded."""
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)
    for cog_name in main._COGS:
        mod = __import__(cog_name, fromlist=["setup"])
        # Some cogs might have async setup, in test synchronous tree registration
        cog_cls = getattr(mod, f"{cog_name.split('.')[-1].replace('_cog', '').title()}Cog", None)
    return bot


class TestSlashCommandParity:
    """Verify that every slash command in README.md is registered in Discord's CommandTree."""

    @pytest.mark.asyncio
    async def test_all_readme_commands_exist_in_tree(self):
        intents = discord.Intents.default()
        bot = commands.Bot(command_prefix="!", intents=intents)
        for cog_name in main._COGS:
            mod = __import__(cog_name, fromlist=["setup"])
            await mod.setup(bot)

        tree_cmds = {cmd.name: cmd for cmd in bot.tree.get_commands()}

        missing = [cmd for cmd in EXPECTED_TOP_LEVEL_COMMANDS if cmd not in tree_cmds]
        assert missing == [], f"Missing slash commands from Discord tree: {missing}"

    @pytest.mark.asyncio
    async def test_subcommand_groups_match_readme(self):
        intents = discord.Intents.default()
        bot = commands.Bot(command_prefix="!", intents=intents)
        for cog_name in main._COGS:
            mod = __import__(cog_name, fromlist=["setup"])
            await mod.setup(bot)

        tree_cmds = {cmd.name: cmd for cmd in bot.tree.get_commands()}

        # 1. Favorite group: add, list, play, remove
        fav_group = tree_cmds.get("favorite")
        assert isinstance(fav_group, discord.app_commands.Group)
        fav_subs = {c.name for c in fav_group.commands}
        assert {"add", "list", "play", "remove"}.issubset(fav_subs)

        # 2. Bookmark group: save, load, list, delete
        bm_group = tree_cmds.get("bookmark")
        assert isinstance(bm_group, discord.app_commands.Group)
        bm_subs = {c.name for c in bm_group.commands}
        assert {"save", "load", "list", "delete"}.issubset(bm_subs)

        # 3. Preset group: load, save, list, delete
        pr_group = tree_cmds.get("preset")
        assert isinstance(pr_group, discord.app_commands.Group)
        pr_subs = {c.name for c in pr_group.commands}
        assert {"load", "save", "list", "delete"}.issubset(pr_subs)

        # 4. Equalizer group: preset, custom, reset, view, show
        eq_group = tree_cmds.get("equalizer")
        assert isinstance(eq_group, discord.app_commands.Group)
        eq_subs = {c.name for c in eq_group.commands}
        assert {"preset", "custom", "reset", "view"}.issubset(eq_subs)

        # 5. Djset group: role, clear
        dj_group = tree_cmds.get("djset")
        assert isinstance(dj_group, discord.app_commands.Group)
        dj_subs = {c.name for c in dj_group.commands}
        assert {"role", "clear"}.issubset(dj_subs)

        # 6. Requestchannel group: set, clear
        rc_group = tree_cmds.get("requestchannel")
        assert isinstance(rc_group, discord.app_commands.Group)
        rc_subs = {c.name for c in rc_group.commands}
        assert {"set", "clear"}.issubset(rc_subs)


# ── 2. /voteskip Command Logic Tests ─────────────────────────────────────────

class TestVoteSkipCommand:
    """Test /voteskip validation, voting quorum, and execution."""

    @pytest.mark.asyncio
    async def test_voteskip_nothing_playing(self):
        bot = MagicMock()
        cog = QueueCog(bot)
        player = MagicMock()
        player.now_playing = None
        bot.get_player.return_value = player
        bot.db = MagicMock()

        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.guild.voice_client = None
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.queue_cog.get_locale", return_value="en"):
            await cog.voteskip.callback(cog, interaction)

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Nothing to Skip" in embed.title

    @pytest.mark.asyncio
    async def test_voteskip_user_not_in_voice(self):
        bot = MagicMock()
        cog = QueueCog(bot)
        player = MagicMock()
        player.now_playing = MagicMock(spec=Track)
        bot.get_player.return_value = player
        bot.db = MagicMock()

        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        vc.channel.name = "MusicVC"

        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.guild.voice_client = vc
        interaction.user.voice = None  # user not in voice
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.queue_cog.get_locale", return_value="en"):
            await cog.voteskip.callback(cog, interaction)

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Voice" in embed.title or "Not in Voice" in embed.title

    @pytest.mark.asyncio
    async def test_voteskip_vote_added_and_view_sent(self):
        bot = MagicMock()
        cog = QueueCog(bot)
        player = MagicMock()
        track = Track(title="Awesome Track", url="https://yt.com/1", duration=180)
        player.now_playing = track
        player.skip_votes = set()
        player.skip_vote_threshold.return_value = 3  # threshold 3
        bot.get_player.return_value = player
        bot.db = MagicMock()

        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.channel = MagicMock()
        vc.channel.name = "MusicVC"
        member1 = MagicMock(bot=False, id=101)
        member2 = MagicMock(bot=False, id=102)
        vc.channel.members = [member1, member2]

        user = MagicMock(id=101, display_name="Alice")
        user.voice.channel = vc.channel

        guild = MagicMock()
        guild.voice_client = vc
        guild.get_member.return_value = user
        bot.get_guild.return_value = guild

        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.guild = guild
        interaction.user = user
        interaction.response.defer = AsyncMock()
        mock_msg = MagicMock()
        interaction.followup.send = AsyncMock(return_value=mock_msg)

        with patch("cogs.queue_cog.get_locale", return_value="en"):
            await cog.voteskip.callback(cog, interaction)

        assert 101 in player.skip_votes
        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        view = interaction.followup.send.call_args[1]["view"]
        assert "Vote Skip" in embed.title
        assert view is not None

    @pytest.mark.asyncio
    async def test_voteskip_threshold_reached_stops_track(self):
        bot = MagicMock()
        cog = QueueCog(bot)
        player = MagicMock()
        track = Track(title="Awesome Track", url="https://yt.com/1", duration=180)
        player.now_playing = track
        player.skip_votes = {101}  # Already 1 vote
        player.skip_vote_threshold.return_value = 2  # threshold 2
        player.cancel_prefetch = MagicMock()
        bot.get_player.return_value = player
        bot.db = MagicMock()

        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.channel = MagicMock()
        vc.channel.members = [MagicMock(bot=False), MagicMock(bot=False)]
        vc.stop = MagicMock()

        user = MagicMock(id=102, display_name="Bob")
        user.voice.channel = vc.channel

        guild = MagicMock()
        guild.voice_client = vc
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.guild = guild
        interaction.user = user
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.queue_cog.get_locale", return_value="en"):
            await cog.voteskip.callback(cog, interaction)

        # 2nd vote reached threshold 2!
        player.cancel_prefetch.assert_called_once()
        vc.stop.assert_called_once()
        assert len(player.skip_votes) == 0
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Skipped" in embed.title


# ── 3. /help Command and UX/UI System Tests ──────────────────────────────────

class TestHelpCommandUXUI:
    """Test /help embed builders and interactive views."""

    def test_overview_embed_structure_and_limits(self):
        embed = build_help_embed("all")
        assert "Gen 4.0" in embed.title
        assert len(embed.fields) >= 9

        # Verify character limit <= 6000
        total_chars = len(embed.title or "") + len(embed.description or "") + len(embed.footer.text or "")
        for f in embed.fields:
            assert len(f.value) <= 1024, f"Field '{f.name}' exceeds 1024 chars ({len(f.value)})"
            total_chars += len(f.name) + len(f.value)

        assert total_chars <= 6000, f"Overview embed exceeds 6000 char limit ({total_chars})"
        assert len(embed.fields) <= 25, f"Overview embed exceeds 25 fields ({len(embed.fields)})"

    @pytest.mark.parametrize("cat_key", _CAT_KEYS)
    def test_category_embed_generation(self, cat_key: str):
        embed = build_help_embed(cat_key)
        assert HELP_CATEGORIES[cat_key]["title"] in embed.title
        assert len(embed.description) > 0
        assert len(embed.description) <= 4096

    def test_help_category_select_options(self):
        select = HelpCategorySelect(current_category="playback")
        assert len(select.options) == 10  # 'all' + 9 categories
        # Check default option
        playback_opt = next(opt for opt in select.options if opt.value == "playback")
        assert playback_opt.default is True

    @pytest.mark.asyncio
    async def test_help_category_select_callback(self):
        select = HelpCategorySelect(current_category="all")
        view = HelpNavigationSelectView(current_category="all")
        select._view = view
        select._values = ["voting"]

        interaction = MagicMock()
        interaction.response.edit_message = AsyncMock()

        await select.callback(interaction)
        assert view.current_category == "voting"
        interaction.response.edit_message.assert_awaited_once()
        called_embed = interaction.response.edit_message.call_args[1]["embed"]
        assert "Democratic Voice Voting" in called_embed.title

    @pytest.mark.asyncio
    async def test_help_navigation_buttons(self):
        view = HelpNavigationSelectView(current_category="playback")
        interaction = MagicMock()
        interaction.response.edit_message = AsyncMock()

        # Click next -> from playback to voting
        next_button = next(child for child in view.children if getattr(child, "custom_id", None) == "help_next")
        await next_button.callback(interaction)
        assert view.current_category == "voting"

        # Click prev -> back to playback
        prev_button = next(child for child in view.children if getattr(child, "custom_id", None) == "help_prev")
        await prev_button.callback(interaction)
        assert view.current_category == "playback"

        # Click overview -> "all"
        overview_button = next(child for child in view.children if getattr(child, "custom_id", None) == "help_overview")
        await overview_button.callback(interaction)
        assert view.current_category == "all"
