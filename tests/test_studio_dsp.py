# -*- coding: utf-8 -*-
"""
tests/test_studio_dsp.py — Unit test suite for Studio DSP & Audio Engine (Gen 5.0).

Tests:
  1. Mid-Side Vocal Remover & Acapella Isolator math & FFmpeg filter generation
  2. Spatial 3D & Binaural Virtualizer acoustic presets
  3. Audio Clarity & Sub-Harmonic Remaster filterchain & level clamping
  4. Multi-Profile EBU R128 Smart Loudnorm (broadcast, podcast, background, party)
  5. Synthetic Audio Chimes (aevalsrc pure math generation)
  6. AudioEffectsProcessor integration & filter ordering
  7. GuildPlayer DSP state lifecycle & reset
  8. Slash Command execution & DJ/voice permission checks
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord

from core.audio import (
    AudioEffectsProcessor,
    SYNTHETIC_CHIME_EXPR,
    build_chime_lavfi_source,
    build_chime_ffmpeg_options,
)
from core.player import GuildPlayer
from models.enums import AudioEffect, AudioQuality

from vocal.filter import build_vocal_filter, MIN_STEREO_LEVEL
from vocal.cog import VocalCog

from spatial.filter import build_spatial_filter, SPATIAL_PRESETS
from spatial.cog import SpatialCog

from remaster.filter import build_clarity_filter, build_remaster_filter, CLARITY_LEVEL_MAP
from remaster.cog import RemasterCog

from loudnorm.filter import build_loudnorm_filter, LOUDNORM_PROFILES
from loudnorm.cog import LoudnormCog

from cogs.playback_cog import PlaybackCog


# ── 1. Vocal Remover & Acapella Tests ─────────────────────────────────────────

class TestVocalDSP:
    def test_vocal_remove_100_percent(self):
        f = build_vocal_filter("remove", 100)
        assert f is not None
        assert f"stereotools=mlev={MIN_STEREO_LEVEL:.6f}:slev=1.0" == f

    def test_vocal_remove_50_percent(self):
        f = build_vocal_filter("remove", 50)
        assert f is not None
        # ratio 0.5 -> mlev ~ 0.507812
        assert "stereotools=mlev=0.507812:slev=1.0" in f

    def test_vocal_remove_boundary_clamping(self):
        # suppression < 1 clamps to 1
        f_low = build_vocal_filter("remove", 0)
        f_1 = build_vocal_filter("remove", 1)
        assert f_low == f_1

        # suppression > 100 clamps to 100
        f_high = build_vocal_filter("remove", 150)
        assert f_high == f"stereotools=mlev={MIN_STEREO_LEVEL:.6f}:slev=1.0"

    def test_vocal_acapella_isolate(self):
        f = build_vocal_filter("isolate")
        assert f is not None
        assert f"stereotools=mlev=1.0:slev={MIN_STEREO_LEVEL:.6f}" in f
        assert "bandpass=frequency=1200:width_type=h:width=2800" in f

    def test_vocal_off_and_none(self):
        assert build_vocal_filter("off") is None
        assert build_vocal_filter(None) is None
        assert build_vocal_filter("disabled") is None


# ── 2. Spatial 3D & Binaural Virtualizer Tests ────────────────────────────────

class TestSpatialDSP:
    def test_spatial_binaural_earwax(self):
        f = build_spatial_filter("binaural")
        assert f == "earwax"

    def test_spatial_hall_preset(self):
        f = build_spatial_filter("hall")
        assert f == "aecho=0.8:0.88:60|120:0.4|0.28,surround=chl_out=stereo"

    def test_spatial_studio_preset(self):
        f = build_spatial_filter("studio")
        assert f == "aecho=0.8:0.7:20:0.25"

    def test_spatial_club_preset(self):
        f = build_spatial_filter("club")
        assert f == "aecho=0.8:0.88:40|80:0.45|0.35,bass=g=4"

    def test_spatial_off(self):
        assert build_spatial_filter("off") is None
        assert build_spatial_filter(None) is None
        assert build_spatial_filter("invalid_xyz") is None


# ── 3. Clarity & Remaster Tests ───────────────────────────────────────────────

class TestRemasterDSP:
    def test_clarity_levels(self):
        assert build_clarity_filter(1) == "crystalizer=i=1.4"
        assert build_clarity_filter(2) == "crystalizer=i=1.8"
        assert build_clarity_filter(3) == "crystalizer=i=2.2"
        assert build_clarity_filter(4) == "crystalizer=i=2.6"
        assert build_clarity_filter(5) == "crystalizer=i=3.0"

    def test_clarity_clamping_safety(self):
        # Must never exceed 3.0 to prevent high frequency noise distortion
        assert build_clarity_filter(10) == "crystalizer=i=3.0"
        assert build_clarity_filter(0) == "crystalizer=i=1.4"

    def test_remaster_enabled_combines_filters(self):
        f = build_remaster_filter(True, clarity_level=3)
        assert f is not None
        assert "crystalizer=i=2.2" in f
        assert "equalizer=f=45:t=q:w=1.2:g=4.5" in f
        assert "compand=attacks=0.05:decays=0.2" in f

    def test_remaster_disabled(self):
        assert build_remaster_filter(False) is None


# ── 4. Multi-Profile EBU R128 Loudnorm Tests ──────────────────────────────────

class TestLoudnormDSP:
    def test_loudnorm_default_broadcast(self):
        f = build_loudnorm_filter()
        assert f == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0"

    def test_loudnorm_podcast_profile(self):
        f = build_loudnorm_filter(profile="podcast")
        assert f == "loudnorm=I=-14.0:TP=-1.0:LRA=7.0"

    def test_loudnorm_background_profile(self):
        f = build_loudnorm_filter(profile="background")
        assert f == "loudnorm=I=-20.0:TP=-2.0:LRA=14.0"

    def test_loudnorm_party_profile(self):
        f = build_loudnorm_filter(profile="party")
        assert f == "loudnorm=I=-12.0:TP=-0.5:LRA=8.0"

    def test_loudnorm_custom_override(self):
        f = build_loudnorm_filter(integrated_loudness=-15.5, true_peak=-0.8, loudness_range=10.0)
        assert f == "loudnorm=I=-15.5:TP=-0.8:LRA=10.0"


# ── 5. Synthetic Audio Chimes Tests ───────────────────────────────────────────

class TestSyntheticChimes:
    def test_chime_source_math_expression(self):
        src = build_chime_lavfi_source()
        assert src.startswith("aevalsrc=")
        assert "880*t" in src
        assert "1760*t" in src
        assert "d=0.35" in src
        assert "c=stereo" in src

    def test_chime_options(self):
        opts = build_chime_ffmpeg_options()
        assert opts["before_options"] == "-nostdin -f lavfi"
        assert opts["options"] == "-vn"


# ── 6. AudioEffectsProcessor Integration Tests ────────────────────────────────

class TestAudioProcessorDSPIntegration:
    def test_filter_ordering_pipeline(self):
        proc = AudioEffectsProcessor()
        opts = proc.build_ffmpeg_options(
            silence_trim=True,
            pitch_semitones=2,
            speed=1.25,
            vocal_filter="stereotools=mlev=0.015625:slev=1.0",
            equalizer_filter="bass=g=5",
            spatial_filter="earwax",
            pan_filter="pan=stereo|c0=0.8*c0|c1=1.0*c1",
            stereo_filter="extrastereo=m=1.5",
            remaster_filter="crystalizer=i=2.0",
            loudnorm_filter="loudnorm=I=-14.0:TP=-1.0:LRA=7.0",
            volume=0.9,
            gapless=True,
        )

        af = opts["options"]
        assert "-af " in af

        # Verify correct acoustic processing sequence
        pos_trim = af.find("silenceremove")
        pos_pitch = af.find("asetrate")
        pos_speed = af.find("atempo")
        pos_vocal = af.find("stereotools")
        pos_eq = af.find("bass=g=5")
        pos_spatial = af.find("earwax")
        pos_pan = af.find("pan=stereo")
        pos_stereo = af.find("extrastereo")
        pos_remaster = af.find("crystalizer")
        pos_loudnorm = af.find("loudnorm=I=-14.0")
        pos_vol = af.find("volume=0.90")

        assert pos_trim < pos_pitch < pos_speed < pos_vocal < pos_eq < pos_spatial < pos_pan < pos_stereo < pos_remaster < pos_loudnorm < pos_vol

        # Verify gapless buffer tuning in before_options
        assert "-probesize 32768" in opts["before_options"]
        assert "-reconnect_delay_max 2" in opts["before_options"]

    def test_standard_buffering_when_not_gapless(self):
        proc = AudioEffectsProcessor()
        opts = proc.build_ffmpeg_options(gapless=False)
        assert "-probesize 65536" in opts["before_options"]
        assert "-reconnect_delay_max 5" in opts["before_options"]


# ── 7. GuildPlayer DSP State & Reset Tests ────────────────────────────────────

class TestGuildPlayerDSPState:
    def test_default_dsp_state(self):
        p = GuildPlayer(12345)
        assert p.vocal_mode is None
        assert p.vocal_suppression == 100
        assert p.spatial_preset is None
        assert p.gapless is False
        assert p.remaster is False
        assert p.clarity_level == 2
        assert p.loudnorm_profile == "broadcast"
        assert p.chimes_enabled is False

    def test_dsp_reset(self):
        p = GuildPlayer(12345)
        p.vocal_mode = "remove"
        p.vocal_suppression = 80
        p.spatial_preset = "hall"
        p.gapless = True
        p.remaster = True
        p.clarity_level = 4
        p.loudnorm_profile = "party"
        p.chimes_enabled = True

        p.reset()

        assert p.vocal_mode is None
        assert p.vocal_suppression == 100
        assert p.spatial_preset is None
        assert p.gapless is False
        assert p.remaster is False
        assert p.clarity_level == 2
        assert p.loudnorm_profile == "broadcast"
        assert p.chimes_enabled is False


# ── 8. Slash Command Logic & Execution Tests ──────────────────────────────────

class TestDSPSlashCommands:
    @pytest.mark.asyncio
    async def test_vocal_command_execution(self):
        bot = MagicMock()
        cog = VocalCog(bot)
        player = GuildPlayer(1001)
        bot.get_player.return_value = player
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))

        interaction = MagicMock()
        interaction.guild_id = 1001
        member = MagicMock(spec=discord.Member)
        member.voice.channel = MagicMock()
        member.guild_permissions.administrator = True
        interaction.user = member
        interaction.guild.voice_client = MagicMock(channel=member.voice.channel)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Execute vocal command with remove mode and 85% suppression
        with patch("vocal.cog.get_locale", return_value="en"):
            await cog.vocal.callback(cog, interaction, mode="remove", suppression=85)

        assert player.vocal_mode == "remove"
        assert player.vocal_suppression == 85
        interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spatial_command_execution(self):
        bot = MagicMock()
        cog = SpatialCog(bot)
        player = GuildPlayer(1001)
        bot.get_player.return_value = player
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))

        interaction = MagicMock()
        interaction.guild_id = 1001
        member = MagicMock(spec=discord.Member)
        member.voice.channel = MagicMock()
        member.guild_permissions.administrator = True
        interaction.user = member
        interaction.guild.voice_client = MagicMock(channel=member.voice.channel)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Execute spatial command with binaural
        with patch("spatial.cog.get_locale", return_value="en"):
            await cog.spatial.callback(cog, interaction, preset="binaural")

        assert player.spatial_preset == "binaural"
        interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remaster_and_clarity_command_execution(self):
        bot = MagicMock()
        cog = RemasterCog(bot)
        player = GuildPlayer(1001)
        bot.get_player.return_value = player
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))

        interaction = MagicMock()
        interaction.guild_id = 1001
        member = MagicMock(spec=discord.Member)
        member.voice.channel = MagicMock()
        member.guild_permissions.administrator = True
        interaction.user = member
        interaction.guild.voice_client = MagicMock(channel=member.voice.channel)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Execute clarity level 4
        with patch("remaster.cog.get_locale", return_value="en"):
            await cog.clarity.callback(cog, interaction, level=4)

        assert player.clarity_level == 4
        assert player.remaster is True

        # Toggle remaster off
        with patch("remaster.cog.get_locale", return_value="en"):
            await cog.remaster.callback(cog, interaction, enabled=False)

        assert player.remaster is False

    @pytest.mark.asyncio
    async def test_gapless_and_chimes_command_execution(self):
        bot = MagicMock()
        cog = PlaybackCog(bot)
        player = GuildPlayer(1001)
        bot.get_player.return_value = player
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))

        interaction = MagicMock()
        interaction.guild_id = 1001
        member = MagicMock(spec=discord.Member)
        member.voice.channel = MagicMock()
        member.guild_permissions.administrator = True
        interaction.user = member
        interaction.guild.voice_client = MagicMock(channel=member.voice.channel)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Toggle gapless
        with patch("cogs.playback_cog.get_locale", return_value="en"):
            await cog.gapless.callback(cog, interaction, enabled=True)
        assert player.gapless is True

        # Toggle chimes
        with patch("cogs.playback_cog.get_locale", return_value="en"):
            await cog.chimes.callback(cog, interaction, enabled=True)
        assert player.chimes_enabled is True

