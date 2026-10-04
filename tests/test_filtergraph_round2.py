# -*- coding: utf-8 -*-
"""
tests/test_filtergraph_round2.py — Unit test suite for Filtergraph Round 2 Audio Features (2.5–2.8).

Covers:
  1. Feature 2.5: Frequency Equalizer & Presets (equalizer/)
  2. Feature 2.6: EBU R128 Smart Loudness Normalization (loudnorm/)
  3. Feature 2.7: Loop A-B Audio Segment Repeat (loop_ab/)
  4. Feature 2.8: Left-Right Balance & Stereo Widening (pan/)
  5. AudioEffectsProcessor integration with new filter options
  6. GuildPlayer state management and reset cleanup
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.audio import AudioEffectsProcessor
from core.player import GuildPlayer
from equalizer.presets import (
    EQ_BANDS,
    EQ_PRESETS,
    PRESET_NAMES,
    build_equalizer_filter,
    clamp_gain,
)
from loudnorm.filter import (
    DEFAULT_LRA,
    DEFAULT_TARGET_I,
    DEFAULT_TRUE_PEAK,
    build_loudnorm_filter,
)
from loop_ab.service import LoopABService
from models.track import Track
from pan.filter import (
    build_pan_filter,
    build_stereo_enhance_filter,
    clamp_balance,
    clamp_width,
)


# ── 1. Feature 2.5: Frequency Equalizer & Presets ─────────────────────────────

class TestEqualizer:
    def test_presets_exist(self):
        for name in ("pop", "rock", "electronic", "vocal", "flat"):
            assert name in EQ_PRESETS
            assert set(EQ_PRESETS[name].keys()) == set(EQ_BANDS.keys())

    def test_clamp_gain(self):
        assert clamp_gain(5.0) == 5.0
        assert clamp_gain(15.0) == 10.0
        assert clamp_gain(-20.0) == -10.0

    def test_build_equalizer_filter_flat_returns_none(self):
        assert build_equalizer_filter(None) is None
        assert build_equalizer_filter({}) is None
        assert build_equalizer_filter(EQ_PRESETS["flat"]) is None

    def test_build_equalizer_filter_pop(self):
        filter_str = build_equalizer_filter(EQ_PRESETS["pop"])
        assert filter_str is not None
        assert "equalizer=f=60:t=q:w=1.0:g=2.0" in filter_str
        assert "equalizer=f=250:t=q:w=1.0:g=1.0" in filter_str
        assert "equalizer=f=1000:t=q:w=1.0:g=2.0" in filter_str
        assert "equalizer=f=8000:t=q:w=1.0:g=3.0" in filter_str

    def test_build_equalizer_filter_rock(self):
        filter_str = build_equalizer_filter(EQ_PRESETS["rock"])
        assert filter_str is not None
        assert "equalizer=f=1000:t=q:w=1.0:g=-1.0" in filter_str

    def test_build_equalizer_filter_clamping(self):
        custom = {"sub_bass": 25.0, "bass": -30.0, "mid": 0.0, "treble": 5.0}
        filter_str = build_equalizer_filter(custom)
        assert filter_str is not None
        assert "g=10.0" in filter_str
        assert "g=-10.0" in filter_str


# ── 2. Feature 2.6: EBU R128 Smart Loudness Normalization ────────────────────

class TestLoudnorm:
    def test_build_loudnorm_filter_default(self):
        f = build_loudnorm_filter()
        assert f == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0"

    def test_build_loudnorm_filter_custom(self):
        f = build_loudnorm_filter(integrated_loudness=-14.0, true_peak=-1.0, loudness_range=9.0)
        assert f == "loudnorm=I=-14.0:TP=-1.0:LRA=9.0"

    def test_audio_processor_loudnorm(self):
        proc = AudioEffectsProcessor()
        opts = proc.build_ffmpeg_options(loudnorm=True)
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in opts["options"]
        # When loudnorm is active, dynaudnorm should NOT be added even if replay_gain=True
        opts2 = proc.build_ffmpeg_options(loudnorm=True, replay_gain=True)
        assert "loudnorm=" in opts2["options"]
        assert "dynaudnorm=" not in opts2["options"]


# ── 3. Feature 2.7: Loop A-B Segment Repeat ──────────────────────────────────

class TestLoopAB:
    @pytest.fixture
    def mock_bot(self):
        bot = MagicMock()
        bot.get_player = MagicMock()
        bot.seek = MagicMock()
        bot.seek.seek_to = AsyncMock(return_value=True)
        return bot

    @pytest.mark.asyncio
    async def test_start_loop_valid(self, mock_bot):
        service = LoopABService(mock_bot)
        player = GuildPlayer(12345)
        player.now_playing = Track(
            title="Test Track",
            url="https://youtube.com/watch?v=123",
            duration=180,
            requested_by_id=1,
            requested_by_name="Tester",
        )
        mock_bot.get_player.return_value = player

        ok, err = await service.start_loop(12345, 30, 90)
        assert ok is True
        assert err == ""
        assert player.loop_ab_range == (30, 90)
        assert player.loop_ab_task is not None
        mock_bot.seek.seek_to.assert_awaited_with(12345, 30)

        # Cleanup task
        player.cancel_loop_ab()

    @pytest.mark.asyncio
    async def test_start_loop_invalid_range(self, mock_bot):
        service = LoopABService(mock_bot)
        player = GuildPlayer(12345)
        player.now_playing = Track(
            title="Test Track",
            url="https://youtube.com/watch?v=123",
            duration=180,
            requested_by_id=1,
            requested_by_name="Tester",
        )
        mock_bot.get_player.return_value = player

        # End <= Start
        ok, err = await service.start_loop(12345, 60, 30)
        assert ok is False
        assert err == "invalid_times"

        # Negative start
        ok, err = await service.start_loop(12345, -5, 30)
        assert ok is False
        assert err == "invalid_times"

    @pytest.mark.asyncio
    async def test_start_loop_no_track(self, mock_bot):
        service = LoopABService(mock_bot)
        player = GuildPlayer(12345)
        player.now_playing = None
        mock_bot.get_player.return_value = player

        ok, err = await service.start_loop(12345, 10, 20)
        assert ok is False
        assert err == "no_track"

    def test_stop_loop(self, mock_bot):
        service = LoopABService(mock_bot)
        player = GuildPlayer(12345)
        mock_bot.get_player.return_value = player

        # No loop active
        assert service.stop_loop(12345) is False

        # Set fake loop
        player.loop_ab_range = (10, 30)
        assert service.stop_loop(12345) is True
        assert player.loop_ab_range is None

    @pytest.mark.asyncio
    async def test_cancel_loop_on_finish_track(self):
        player = GuildPlayer(12345)
        player.loop_ab_range = (10, 30)
        await player.finish_track()
        assert player.loop_ab_range is None

    def test_cancel_loop_on_reset(self):
        player = GuildPlayer(12345)
        player.loop_ab_range = (10, 30)
        player.equalizer_bands = {"mid": 2.0}
        player.equalizer_preset = "pop"
        player.loudnorm = True
        player.pan_balance = 0.5
        player.stereo_width = 1.5

        player.reset()

        assert player.loop_ab_range is None
        assert player.equalizer_bands == {}
        assert player.equalizer_preset is None
        assert player.loudnorm is False
        assert player.pan_balance == 0.0
        assert player.stereo_width == 1.0


# ── 4. Feature 2.8: Pan Balance & Stereo Widening ────────────────────────────

class TestPanAndStereo:
    def test_clamp_balance(self):
        assert clamp_balance(0.0) == 0.0
        assert clamp_balance(-1.5) == -1.0
        assert clamp_balance(2.0) == 1.0
        assert clamp_balance(-0.5) == -0.5

    def test_build_pan_filter_center_returns_none(self):
        assert build_pan_filter(0.0) is None
        assert build_pan_filter(0.01) is None
        assert build_pan_filter(-0.01) is None

    def test_build_pan_filter_left(self):
        # 50% Left -> Left=1.0, Right=0.5
        f = build_pan_filter(-0.5)
        assert f == "pan=stereo|c0=1.00*c0|c1=0.50*c1"

        # 100% Left -> Left=1.0, Right=0.0
        f_full = build_pan_filter(-1.0)
        assert f_full == "pan=stereo|c0=1.00*c0|c1=0.00*c1"

    def test_build_pan_filter_right(self):
        # 50% Right -> Left=0.5, Right=1.0
        f = build_pan_filter(0.5)
        assert f == "pan=stereo|c0=0.50*c0|c1=1.00*c1"

        # 100% Right -> Left=0.0, Right=1.0
        f_full = build_pan_filter(1.0)
        assert f_full == "pan=stereo|c0=0.00*c0|c1=1.00*c1"

    def test_build_stereo_enhance_filter(self):
        # Normal 1.0x returns None (bypassed)
        assert build_stereo_enhance_filter(1.0) is None
        assert build_stereo_enhance_filter(1.02) is None

        # Wide 1.5x
        f_wide = build_stereo_enhance_filter(1.5)
        assert f_wide == "extrastereo=m=1.50"

        # Mono 0.0x
        f_mono = build_stereo_enhance_filter(0.0)
        assert f_mono == "extrastereo=m=0.00"

        # Clamping
        f_clamped = build_stereo_enhance_filter(5.0)
        assert f_clamped == "extrastereo=m=3.00"


# ── 5. AudioEffectsProcessor Integration ──────────────────────────────────────

class TestAudioEffectsProcessorIntegration:
    def test_full_filter_chain(self):
        proc = AudioEffectsProcessor()
        eq_str = build_equalizer_filter(EQ_PRESETS["rock"])
        pan_str = build_pan_filter(-0.5)
        stereo_str = build_stereo_enhance_filter(1.5)

        opts = proc.build_ffmpeg_options(
            volume=0.8,
            speed=1.25,
            pitch_semitones=2,
            equalizer_filter=eq_str,
            pan_filter=pan_str,
            stereo_filter=stereo_str,
            loudnorm=True,
            to_seconds=45,
        )

        af = opts["options"]
        assert "atempo=1.25" in af
        assert "asetrate=" in af
        assert "equalizer=f=60" in af
        assert "pan=stereo" in af
        assert "extrastereo=m=1.50" in af
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in af
        assert "volume=0.80" in af
        assert "-to 45" in af
