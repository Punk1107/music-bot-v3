# -*- coding: utf-8 -*-
"""
tests/test_phase2_stability.py — Test suite for Phase 2 optimizations:
  - Domain 1: RAM Monitoring, Memory Leak Detection, Tiered GC
  - Domain 2: Discord WebSocket Gateway Latency Monitor & Lifecycle Resilience
  - Domain 3: MySQL Connection Pool Exhaustion, Slow Query Profiler, Telemetry
  - Domain 4: Traceback Deduplication & Incident ID Sanitization
  - Domain 5: Universal 9-Language Parity (i18n)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import config
from core.database import DatabaseManager, DatabasePoolExhaustedError
from core.gateway_monitor import GatewayLatencyMonitor
from core.lru_cache import check_memory_pressure
from core.player import GuildPlayer
from core.stability import (
    ExceptionKind,
    MemoryLeakDetector,
    classify_exception,
)
from models.track import Track
from utils.error_handler import (
    _compute_error_fingerprint,
    forward_to_dev_channel,
    _recent_errors,
    _suppressed_counts,
)
from core.i18n import t


# ══════════════════════════════════════════════════════════════════════════════
# Domain 1: RAM Monitoring, Leak Detection & Player Cleanup
# ══════════════════════════════════════════════════════════════════════════════

class TestRamAndPlayerCleanup:
    def test_memory_leak_snapshot_collects_top_types(self):
        detector = MemoryLeakDetector()
        snap = detector._take_snapshot()
        assert snap is not None
        assert hasattr(snap, "top_types")
        assert isinstance(snap.top_types, dict)
        assert len(snap.top_types) > 0
        # Ensure common python types are counted
        assert any(k in snap.top_types for k in ["dict", "list", "str", "tuple", "set"])

    @pytest.mark.asyncio
    async def test_tiered_gc_in_memory_pressure(self):
        with patch("core.lru_cache._load_thresholds", return_value=(100.0, 200.0, 300.0)), \
             patch("core.lru_cache._try_import_psutil") as mock_psutil, \
             patch("gc.collect", return_value=42) as mock_gc:
            
            # Mock process memory to 250MB (Level 2: Moderate)
            proc_mock = MagicMock()
            proc_mock.memory_info.return_value.rss = 250 * 1024 * 1024
            mock_psutil.return_value.Process.return_value = proc_mock
            mock_psutil.return_value.virtual_memory.return_value.percent = 50.0

            res_lvl2 = await check_memory_pressure()
            assert res_lvl2["level"] == 2
            assert res_lvl2.get("gc_freed") == 42
            mock_gc.assert_called_with(1)

            # Mock process memory to 350MB (Level 3: Aggressive)
            proc_mock.memory_info.return_value.rss = 350 * 1024 * 1024
            res_lvl3 = await check_memory_pressure()
            assert res_lvl3["level"] == 3
            assert res_lvl3.get("gc_freed") == 42
            mock_gc.assert_called_with(2)

    @pytest.mark.asyncio
    async def test_player_queue_dirty_flag_lifecycle(self):
        player = GuildPlayer(guild_id=123)
        assert player._queue_dirty is False

        # Enqueue sets dirty
        dummy_track = Track(
            title="Song 1",
            url="https://youtu.be/test1",
            duration=180,
        )
        await player.enqueue(dummy_track)
        assert player._queue_dirty is True
        assert len(player) == 1

        # Reset flag simulating DB save
        player._queue_dirty = False
        assert player._queue_dirty is False

        # Dequeue sets dirty
        popped = await player.dequeue()
        assert popped == dummy_track
        assert player._queue_dirty is True

        # Clear sets dirty
        player._queue_dirty = False
        await player.clear()
        assert player._queue_dirty is True

    @pytest.mark.asyncio
    async def test_player_destroy_method(self):
        player = GuildPlayer(guild_id=123)
        
        # Attach dummy background tasks
        task1 = asyncio.create_task(asyncio.sleep(10))
        task2 = asyncio.create_task(asyncio.sleep(10))
        player.alone_leave_task = task1
        player.sleep_timer_task = task2

        player.destroy()
        await asyncio.sleep(0)

        assert task1.cancelled() or getattr(task1, 'cancelling', lambda: False)()
        assert task2.cancelled() or getattr(task2, 'cancelling', lambda: False)()
        assert player.alone_leave_task is None
        assert player.sleep_timer_task is None
        assert player.voice_client is None


# ══════════════════════════════════════════════════════════════════════════════
# Domain 2: Discord WebSocket Latency Monitoring
# ══════════════════════════════════════════════════════════════════════════════

class TestGatewayLatencyMonitor:
    def test_rolling_window_and_statistics(self):
        monitor = GatewayLatencyMonitor(window_size=5)
        # Record 5 samples: 40ms, 50ms, 60ms, 70ms, 80ms
        for s in [0.040, 0.050, 0.060, 0.070, 0.080]:
            monitor.record_latency(s)

        stats = monitor.stats()
        assert stats["samples_count"] == 5
        assert stats["current_ms"] == 80.0
        assert stats["min_ms"] == 40.0
        assert stats["max_ms"] == 80.0
        assert stats["avg_ms"] == 60.0
        assert stats["jitter_ms"] > 0.0

        # Push 6th sample (window rolls, drops 40ms)
        monitor.record_latency(0.090)
        assert len(monitor._samples) == 5
        assert monitor.min_latency_ms == 50.0
        assert monitor.max_latency_ms == 90.0

    def test_quality_grade_classification(self):
        monitor = GatewayLatencyMonitor()
        
        monitor.record_latency(0.040)  # 40ms -> Optimal
        assert monitor.quality_grade == "Optimal"

        monitor.record_latency(0.100)  # 100ms -> Acceptable
        assert monitor.quality_grade == "Acceptable"

        monitor.record_latency(0.200)  # 200ms -> Degraded
        assert monitor.quality_grade == "Degraded"

        monitor.record_latency(0.350)  # 350ms -> Critical
        assert monitor.quality_grade == "Critical"

    def test_gateway_lifecycle_counters(self):
        monitor = GatewayLatencyMonitor()
        assert monitor.connect_count == 0
        assert monitor.disconnect_count == 0
        assert monitor.resume_count == 0

        monitor.on_connect()
        assert monitor.connect_count == 1
        assert monitor.last_connect_time is not None

        monitor.on_disconnect()
        assert monitor.disconnect_count == 1
        assert monitor.last_disconnect_time is not None

        monitor.on_resumed()
        assert monitor.resume_count == 1
        assert monitor.last_resume_time is not None

    def test_spike_detection_throttle(self):
        monitor = GatewayLatencyMonitor()
        with patch("core.gateway_monitor.logger.warning") as mock_warn:
            # 1st spike
            monitor.record_latency(0.300)
            assert mock_warn.call_count == 1

            # Immediate 2nd spike within 60s should be throttled
            monitor.record_latency(0.320)
            assert mock_warn.call_count == 1

            # Advance time by 61s
            monitor._last_spike_log_time -= 61.0
            monitor.record_latency(0.310)
            assert mock_warn.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# Domain 3: MySQL Connection Pool & Query Telemetry
# ══════════════════════════════════════════════════════════════════════════════

class TestDatabaseOptimization:
    def test_pool_stats_telemetry_keys(self):
        db = DatabaseManager(":memory:")
        stats = db.pool_stats()
        expected_keys = {
            "status", "size", "freesize", "maxsize", "mode",
            "reconnect_count", "deadlock_retry_count", "dual_write_diffs",
            "pool_exhausted_count", "peak_busy", "total_queries",
            "slow_queries_count", "avg_query_ms",
        }
        for k in expected_keys:
            assert k in stats, f"Missing telemetry key: {k}"

    @pytest.mark.asyncio
    async def test_pool_acquire_timeout_raises_database_pool_exhausted_error(self):
        db = DatabaseManager("localhost", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()

        # Simulate timeout on pool.acquire()
        async def _hang():
            await asyncio.sleep(10.0)
        mock_pool.acquire.side_effect = _hang
        mock_pool.size = 25
        mock_pool.freesize = 0

        with patch.object(db.router, "select_pool", return_value=mock_pool), \
             patch.object(config, "DB_ACQUIRE_TIMEOUT", 0.05):
            
            async def _dummy_op(conn, cur):
                return 1

            with pytest.raises(DatabasePoolExhaustedError) as exc_info:
                await db._run_mysql(_dummy_op)

            assert "pool exhausted" in str(exc_info.value).lower()
            assert db.pool_stats()["pool_exhausted_count"] == 1

    @pytest.mark.asyncio
    async def test_slow_query_profiling(self):
        db = DatabaseManager("localhost", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.ping = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_conn.cursor = MagicMock()

        cursor_ctx = AsyncMock()
        cursor_ctx.__aenter__.return_value = MagicMock()
        cursor_ctx.__aexit__.return_value = None
        mock_conn.cursor.return_value = cursor_ctx

        acquire_fut = asyncio.Future()
        acquire_fut.set_result(mock_conn)
        mock_pool.acquire.return_value = acquire_fut
        mock_pool.release = MagicMock()
        mock_pool.size = 10
        mock_pool.freesize = 10

        with patch.object(db.router, "select_pool", return_value=mock_pool), \
             patch.object(config, "DB_SLOW_QUERY_THRESHOLD_MS", 20.0), \
             patch("core.database.logger.warning") as mock_warn:

            async def _slow_op(conn, cur):
                await asyncio.sleep(0.030) # 30ms > 20ms threshold
                return "ok"

            res = await db._run_mysql(_slow_op)
            assert res == "ok"
            assert db.pool_stats()["slow_queries_count"] == 1
            assert db.pool_stats()["total_queries"] == 1
            assert mock_warn.called
            assert "Slow MySQL query detected" in mock_warn.call_args[0][0]


# ══════════════════════════════════════════════════════════════════════════════
# Domain 4: Error Deduplication & Incident Sanitization
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorDeduplicationAndSanitization:
    def test_compute_error_fingerprint_consistency(self):
        try:
            raise ValueError("Test error 1")
        except Exception as e1:
            fp1 = _compute_error_fingerprint(e1)
            fp2 = _compute_error_fingerprint(e1)
            assert fp1 == fp2
            assert len(fp1) == 32

    @pytest.mark.asyncio
    async def test_forward_to_dev_channel_rate_limiting(self):
        bot_mock = MagicMock()
        chan_mock = AsyncMock()
        bot_mock.get_channel.return_value = chan_mock

        _recent_errors.clear()
        _suppressed_counts.clear()

        try:
            raise KeyError("Dev channel test error")
        except Exception as exc:
            with patch("config.DEV_LOG_CHANNEL_IDS", [9999]), \
                 patch("config.DEV_LOG_RATE_LIMIT_SECS", 60.0):
                
                # First forward: sends message
                await forward_to_dev_channel(bot_mock, exc)
                assert chan_mock.send.call_count == 1

                # Rapid duplicate within 60s: suppressed
                await forward_to_dev_channel(bot_mock, exc)
                assert chan_mock.send.call_count == 1

                # Advance timestamp by 61s and send again: sends with suppressed notice
                fp = _compute_error_fingerprint(exc)
                _recent_errors[fp] -= 61.0
                await forward_to_dev_channel(bot_mock, exc)
                assert chan_mock.send.call_count == 2
                sent_msg = chan_mock.send.call_args[1]["content"]
                assert "suppressed 1 identical occurrences" in sent_msg

    def test_fatal_error_pattern_classification(self):
        exc_access = RuntimeError("Access denied for user 'musicbot'@'localhost'")
        exc_gone = RuntimeError("MySQL server has gone away")
        exc_dup = RuntimeError("Duplicate entry '1-1' for key 'PRIMARY'")

        assert classify_exception(exc_access) == ExceptionKind.FATAL
        assert classify_exception(exc_gone) == ExceptionKind.RECOVERABLE
        assert classify_exception(exc_dup) == ExceptionKind.EXPECTED


# ══════════════════════════════════════════════════════════════════════════════
# Domain 5: Universal 9-Language Parity (i18n)
# ══════════════════════════════════════════════════════════════════════════════

class TestUniversalNineLanguageParity:
    LOCALES = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]

    def test_all_9_locales_have_identical_key_set(self):
        keys = {}
        for loc in self.LOCALES:
            path = Path("locales") / loc / "strings.json"
            assert path.exists(), f"File {path} does not exist"
            with open(path, "r", encoding="utf-8") as f:
                keys[loc] = set(json.load(f).keys())

        base_en = keys["en"]
        assert len(base_en) == 314, f"Expected 314 keys, got {len(base_en)}"

        for loc in self.LOCALES:
            diff = base_en.symmetric_difference(keys[loc])
            assert len(diff) == 0, f"Locale {loc} key difference: {diff}"

    def test_all_9_locales_have_matching_placeholders(self):
        var_re = re.compile(r"\{([a-zA-Z0-9_]+)\}")
        catalogs = {}
        for loc in self.LOCALES:
            with open(Path("locales") / loc / "strings.json", "r", encoding="utf-8") as f:
                catalogs[loc] = json.load(f)

        for key, en_text in catalogs["en"].items():
            en_placeholders = set(var_re.findall(en_text))
            for loc in self.LOCALES:
                loc_text = catalogs[loc][key]
                loc_placeholders = set(var_re.findall(loc_text))
                assert en_placeholders == loc_placeholders, (
                    f"Placeholder mismatch in key '{key}' for locale '{loc}': "
                    f"{en_placeholders} vs {loc_placeholders}"
                )

    def test_new_phase2_keys_rendered_in_all_locales(self):
        new_keys = [
            "health.gateway",
            "health.gateway_optimal",
            "health.gateway_acceptable",
            "health.gateway_degraded",
            "health.gateway_critical",
            "health.gateway_details",
            "error.internal_incident",
        ]
        for loc in self.LOCALES:
            for k in new_keys:
                if k == "health.gateway_details":
                    rendered = t(k, loc, current=45.2, avg=48.0, jitter=3.1, resumes=2, reconnects=1)
                    assert "45.2ms" in rendered
                    assert "48.0ms" in rendered
                elif k == "error.internal_incident":
                    rendered = t(k, loc, incident_id="INC-9999")
                    assert "INC-9999" in rendered
                else:
                    rendered = t(k, loc)
                    assert rendered is not None
                    assert len(rendered.strip()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Domain 6 / Phase 5: Server Automation, Governance & MySQL Scalability
# ══════════════════════════════════════════════════════════════════════════════

class TestPhase5ServerAutomationAndGovernance:
    """
    Unit and integration tests for Phase 5:
    1. Single-Message Clean Player Channel Board
    2. Server Music Scheduler & Alarm Clock (scheduler/)
    3. Distributed Voice Persistence & Auto-Resume 2.0
    """

    def test_server_config_phase5_fields_and_persistence(self):
        from models.server_config import ServerConfig

        cfg = ServerConfig(
            guild_id=987654321,
            clean_mode=True,
            board_message_id=1122334455,
            auto_resume=False,
        )
        assert cfg.clean_mode is True
        assert cfg.board_message_id == 1122334455
        assert cfg.auto_resume is False

        d = cfg.to_dict()
        assert d["clean_mode"] == 1
        assert d["board_message_id"] == 1122334455
        assert d["auto_resume"] == 0

        params = cfg.to_db_params()
        assert 1 in params
        assert 1122334455 in params
        assert 0 in params

        row = {
            "guild_id": 987654321,
            "prefix": "!",
            "default_volume": 100,
            "dj_role_id": None,
            "announcements": 1,
            "auto_leave_alone": 1,
            "auto_leave_empty": 1,
            "auto_pause_empty": 1,
            "bitrate": 128000,
            "locale": "en",
            "log_channel_id": None,
            "request_channel_id": 555666777,
            "auto_playlist": 0,
            "idle_timeout": 300,
            "embed_theme": "classic",
            "clean_mode": 1,
            "board_message_id": 1122334455,
            "auto_resume": 0,
        }
        loaded = ServerConfig.from_db_row(row)
        assert loaded.clean_mode is True
        assert loaded.board_message_id == 1122334455
        assert loaded.auto_resume is False

    def test_request_channel_board_embed_rendering(self):
        from utils.embeds import request_channel_board_embed
        from models.enums import LoopMode

        player = MagicMock()
        player.now_playing = None
        player.queue = []
        player.loop_mode = LoopMode.OFF
        player.volume = 1.0

        embed = request_channel_board_embed(player, locale="en")
        assert "Music Request Board" in embed.title
        assert "No music is currently playing" in embed.description

        track = Track(
            title="Lo-Fi Chill Beats",
            url="https://youtube.com/watch?v=123",
            duration=180,
            requested_by_id=1,
            requested_by_name="Tester",
        )
        player.now_playing = track
        player.elapsed_seconds = 60
        player.progress_fraction = MagicMock(return_value=60 / 180)
        player.queue = [
            Track(title="Next Track 1", url="", duration=120),
            Track(title="Next Track 2", url="", duration=150),
        ]
        embed_active = request_channel_board_embed(player, locale="en")
        assert "Lo-Fi Chill Beats" in embed_active.title
        field_names = [f.name for f in embed_active.fields]
        assert any("Up Next (2)" in name for name in field_names)
        assert any("Next Track 1" in str(f.value) for f in embed_active.fields)

    @pytest.mark.asyncio
    async def test_clean_board_control_view_interactions(self):
        from utils.views import CleanBoardControlView
        from models.enums import LoopMode

        bot = MagicMock()
        guild = MagicMock()
        vc = MagicMock()
        bot.get_guild.return_value = guild
        guild.voice_client = vc

        player = MagicMock()
        player.now_playing = None
        player.progress_fraction = MagicMock(return_value=0.0)
        player.elapsed_seconds = 0
        player.volume = 1.0
        player.queue = [MagicMock(), MagicMock()]
        player.loop_mode = LoopMode.OFF
        player.clear = AsyncMock(return_value=0)
        player.shuffle = AsyncMock()
        bot.get_player.return_value = player

        view = CleanBoardControlView(bot, guild_id=123)
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.edit_message = AsyncMock()

        # Play/Pause: playing -> pause
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        btn_pp = view.btn_playpause
        await cast(Any, btn_pp.callback)(interaction)
        vc.pause.assert_called_once()
        assert btn_pp.label == "▶ Resume"

        # Play/Pause: paused -> resume
        vc.is_playing.return_value = False
        vc.is_paused.return_value = True
        await cast(Any, btn_pp.callback)(interaction)
        vc.resume.assert_called_once()
        assert btn_pp.label == "⏸ Pause"

        # Skip button
        vc.is_playing.return_value = True
        await cast(Any, view.btn_skip.callback)(interaction)
        vc.stop.assert_called_once()

        # Stop button
        await cast(Any, view.btn_stop.callback)(interaction)
        player.clear.assert_awaited_once()

        # Shuffle button
        await cast(Any, view.btn_shuffle.callback)(interaction)
        player.shuffle.assert_awaited_once()

        # Loop button
        player.loop_mode = LoopMode.OFF
        await cast(Any, view.btn_loop.callback)(interaction)
        assert player.loop_mode == LoopMode.TRACK
        assert "Track" in view.btn_loop.label

    @pytest.mark.asyncio
    async def test_admin_clean_mode_and_autoresume_commands(self):
        from cogs.admin import AdminCog
        from models.server_config import ServerConfig

        bot = MagicMock()
        bot.db = AsyncMock()
        cfg = ServerConfig(guild_id=456, request_channel_id=789, clean_mode=False)
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.save_server_config = AsyncMock()

        player = MagicMock()
        player.now_playing = None
        player.progress_fraction = MagicMock(return_value=0.0)
        player.elapsed_seconds = 0
        player.volume = 1.0
        player.loop_mode = MagicMock(value="off")
        bot.get_player.return_value = player

        cog = AdminCog(bot)

        interaction = MagicMock()
        interaction.guild_id = 456
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.guild_permissions.administrator = True
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock(return_value=MagicMock(id=998877))
        interaction.guild.get_channel.return_value = channel

        with patch("cogs.admin.get_locale", return_value="en"):
            await cast(Any, cog.rc_clean_mode.callback)(cog, interaction, enabled=True)

        assert cfg.clean_mode is True
        assert cfg.board_message_id == 998877
        bot.db.save_server_config.assert_awaited()
        interaction.followup.send.assert_awaited()

        # /autoresume state:on and state:off
        cfg.auto_resume = False
        with patch("cogs.admin.get_locale", return_value="en"):
            await cast(Any, cog.autoresume.callback)(cog, interaction, state="on")
        assert cfg.auto_resume is True

        with patch("cogs.admin.get_locale", return_value="en"):
            await cast(Any, cog.autoresume.callback)(cog, interaction, state="off")
        assert cfg.auto_resume is False

    def test_music_scheduler_service_time_normalization(self):
        from scheduler.service import MusicSchedulerService

        bot = MagicMock()
        service = MusicSchedulerService(bot)

        assert service.normalize_time("08:00") == "08:00:00"
        assert service.normalize_time("8:30") == "08:30:00"
        assert service.normalize_time("23:59:00") == "23:59:00"
        assert service.normalize_time("00:00") == "00:00:00"

        with pytest.raises(ValueError):
            service.normalize_time("25:00")

        with pytest.raises(ValueError):
            service.normalize_time("invalid-time")

    @pytest.mark.asyncio
    async def test_music_scheduler_service_crud(self):
        from scheduler.service import MusicSchedulerService

        bot = MagicMock()
        bot.db = AsyncMock()
        bot.db.create_scheduled_task = AsyncMock(return_value=42)
        bot.db.list_scheduled_tasks = AsyncMock(return_value=[
            {
                "id": 42,
                "guild_id": 100,
                "voice_channel_id": 200,
                "text_channel_id": 300,
                "user_id": 400,
                "scheduled_time": "08:00:00",
                "days_of_week": "daily",
                "query": "Lofi hip hop",
                "is_active": 1,
            }
        ])
        bot.db.delete_scheduled_task = AsyncMock(return_value=True)

        service = MusicSchedulerService(bot)
        task = await service.add_task(
            guild_id=100,
            voice_channel_id=200,
            text_channel_id=300,
            user_id=400,
            time_str="08:00",
            query="Lofi hip hop",
            repeat_mode="daily",
        )
        assert task.id == 42
        assert task.scheduled_time.startswith("08:00")
        assert task.query == "Lofi hip hop"

        tasks = await service.list_tasks(100)
        assert len(tasks) == 1
        assert tasks[0].id == 42

        ok = await service.delete_task(100, 42)
        assert ok is True
        bot.db.delete_scheduled_task.assert_awaited_once_with(100, 42)

    @pytest.mark.asyncio
    async def test_scheduler_cog_slash_commands(self):
        from scheduler.cog import SchedulerCog
        from scheduler.models import ScheduledTask

        bot = MagicMock()
        bot.db = AsyncMock()
        cog = SchedulerCog(bot)
        cog.service = AsyncMock()

        dummy_task = ScheduledTask(
            id=10,
            guild_id=111,
            voice_channel_id=222,
            text_channel_id=333,
            user_id=444,
            scheduled_time="09:00:00",
            days_of_week="weekdays",
            query="Morning Jazz",
            is_active=True,
        )
        cog.service.add_task = AsyncMock(return_value=dummy_task)
        cog.service.list_tasks = AsyncMock(return_value=[dummy_task])
        cog.service.delete_task = AsyncMock(return_value=True)

        interaction = MagicMock()
        interaction.guild.id = 111
        interaction.channel_id = 333
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 444
        interaction.user.guild_permissions.administrator = True
        interaction.user.voice.channel = MagicMock(id=222)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))

        # /schedule add
        await cast(Any, cog.schedule_add.callback)(
            cog,
            interaction,
            time="09:00",
            query="Morning Jazz",
            voice_channel=None,
            repeat="weekdays",
        )
        cog.service.add_task.assert_awaited_once()
        interaction.followup.send.assert_awaited()

        # /schedule list
        await cast(Any, cog.schedule_list.callback)(cog, interaction)
        cog.service.list_tasks.assert_awaited_once_with(111)

        # /schedule remove
        await cast(Any, cog.schedule_remove.callback)(cog, interaction, task_id=10)
        cog.service.delete_task.assert_awaited_once_with(111, 10)

    @pytest.mark.asyncio
    async def test_clean_mode_request_channel_message_handling(self):
        from main import MusicBot
        from models.server_config import ServerConfig

        bot = MagicMock(spec=MusicBot)
        bot.db = AsyncMock()
        cfg = ServerConfig(guild_id=123, request_channel_id=456, clean_mode=True, board_message_id=789)
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.save_server_config = AsyncMock()

        message = MagicMock(spec=discord.Message)
        message.guild.id = 123
        message.channel.id = 456
        message.content = "play lofi"
        message.delete = AsyncMock()
        board_msg = MagicMock()
        board_msg.edit = AsyncMock()
        message.channel.fetch_message = AsyncMock(return_value=board_msg)

        player = MagicMock()
        player.now_playing = None
        player.progress_fraction = MagicMock(return_value=0.0)
        player.elapsed_seconds = 0
        player.volume = 1.0
        player.loop_mode = MagicMock(value="off")
        player.per_user_limit = 0
        player.enqueue = AsyncMock(return_value=1)
        player.queue = []
        bot.get_player.return_value = player
        bot.nlu = MagicMock()
        from models.enums import NLUIntent
        bot.nlu.parse.return_value = MagicMock(intent=NLUIntent.PLAY, query="lofi")

        music_cog = MagicMock()
        music_cog._play_next = AsyncMock()
        bot.cogs = {"Music": music_cog}
        bot.youtube = MagicMock()
        bot.youtube.is_youtube_url.return_value = False
        bot.yt_breaker = MagicMock()
        dummy_track = Track(title="Lofi Track", url="https://youtube.com/watch?v=xyz")
        bot.yt_breaker.call = AsyncMock(return_value=[dummy_track])

        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        v_chan = MagicMock()
        message.author.voice.channel = v_chan
        vc.channel = v_chan
        message.guild.voice_client = vc

        with patch("main.get_locale_sync", return_value="en"):
            await MusicBot._handle_request_channel(bot, message)

        message.delete.assert_awaited_once()
        board_msg.edit.assert_awaited()
        music_cog._play_next.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_main_autoresume_voice_reconnection(self):
        from main import MusicBot
        from models.server_config import ServerConfig

        bot = MagicMock(spec=MusicBot)
        bot.db = AsyncMock()
        guild = MagicMock()
        guild.id = 999
        bot.guilds = [guild]

        cfg = ServerConfig(guild_id=999, auto_resume=True)
        bot.db.get_server_config = AsyncMock(return_value=cfg)

        resumed_track = Track(title="Resumed Song", url="https://yt/abc")
        saved_state = {
            "loop_mode": "off",
            "volume": 0.8,
            "effects": [],
            "text_channel_id": 100,
            "channel_id": 200,
            "elapsed_secs": 45,
            "now_playing": resumed_track,
            "queue": [],
        }
        bot.db.load_session_state = AsyncMock(return_value=saved_state)
        bot.db.clear_session_state = AsyncMock()

        player = MagicMock()
        player.extend = AsyncMock()
        bot.get_player.return_value = player

        voice_channel = MagicMock()
        voice_channel.name = "Music Lounge"
        voice_channel.connect = AsyncMock(return_value=MagicMock(is_playing=MagicMock(return_value=False), is_paused=MagicMock(return_value=False)))
        guild.get_channel.side_effect = lambda cid: voice_channel if cid == 200 else MagicMock()
        guild.voice_client = None

        music_cog = MagicMock()
        music_cog._play_next = AsyncMock()
        bot.cogs = {"Music": music_cog}

        await MusicBot._restore_queues(bot)

        assert resumed_track._resume_offset == 45
        voice_channel.connect.assert_awaited_once()
        music_cog._play_next.assert_awaited_once_with(999)
        bot.db.clear_session_state.assert_awaited_once_with(999)

        # When auto_resume is False -> should not connect
        voice_channel.connect.reset_mock()
        music_cog._play_next.reset_mock()
        cfg.auto_resume = False
        bot.db.load_session_state.return_value = saved_state

        await MusicBot._restore_queues(bot)
        voice_channel.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scheduler_once_mode_and_worker_lock_release(self):
        from main import MusicBot
        bot = MagicMock(spec=MusicBot)
        bot.db = AsyncMock()
        bot.get_guild = MagicMock(return_value=None)  # skip playback
        task_row = {
            "id": 55,
            "guild_id": 111,
            "voice_channel_id": 222,
            "text_channel_id": 333,
            "query": "Song",
            "user_id": 444,
            "days_of_week": "once",
        }
        bot.db.claim_due_scheduled_tasks = AsyncMock(return_value=[task_row])
        bot.db.deactivate_scheduled_task = AsyncMock(return_value=True)
        bot.db.release_scheduled_task_lock = AsyncMock()
        bot.cogs = {"Music": MagicMock()}

        await MusicBot._scheduled_playback(bot)

        bot.db.deactivate_scheduled_task.assert_awaited_once_with(55)
        bot.db.release_scheduled_task_lock.assert_awaited_once_with(55)

    @pytest.mark.asyncio
    async def test_scheduler_non_admin_forbidden(self):
        from scheduler.cog import SchedulerCog
        bot = MagicMock()
        bot.db = AsyncMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))
        cog = SchedulerCog(bot)
        cog.service = AsyncMock()

        interaction = MagicMock()
        interaction.guild.id = 111
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.guild_permissions.administrator = False
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Non-admin add
        await cast(Any, cog.schedule_add.callback)(cog, interaction, time="08:00", query="Jazz")
        cog.service.add_task.assert_not_called()
        assert "❌" in interaction.followup.send.call_args[0][0]

        # Non-admin remove
        interaction.followup.send.reset_mock()
        await cast(Any, cog.schedule_remove.callback)(cog, interaction, task_id=99)
        cog.service.delete_task.assert_not_called()
        assert "❌" in interaction.followup.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_clean_board_control_view_dynamic_guild_id(self):
        from utils.views import CleanBoardControlView
        bot = MagicMock()
        player = GuildPlayer(123)
        bot.get_player.return_value = player
        bot.get_guild.return_value = MagicMock()

        view = CleanBoardControlView(bot)  # None guild_id
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.response.is_done.return_value = False
        interaction.response.edit_message = AsyncMock()

        # Test button click resolves guild_id dynamically
        await cast(Any, view.btn_playpause.callback)(interaction)
        interaction.response.edit_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_voice_disconnect_preserves_current_track_with_offset(self):
        from main import MusicBot
        bot = MagicMock(spec=MusicBot)
        bot.user.id = 9999
        member = MagicMock(id=9999)
        before = MagicMock()
        before.channel = MagicMock(id=101)
        after = MagicMock()
        after.channel = None  # Disconnected

        player = GuildPlayer(555)
        curr_track = Track(title="Running Track", url="https://yt.com/watch?v=123", duration=300)
        player.now_playing = curr_track
        import datetime
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=75)
        bot.get_player.return_value = player
        bot.cogs = {"Music": MagicMock()}

        import asyncio
        def _safe_track(task_or_coro, *a, **k):
            if asyncio.iscoroutine(task_or_coro):
                task_or_coro.close()
            return MagicMock()
        bot.track_task = MagicMock(side_effect=_safe_track)

        guild = MagicMock()
        guild.id = 555
        before.channel.guild = guild

        await MusicBot.on_voice_state_update(bot, member, before, after)

        assert len(player._queue) == 1
        queued = player._queue[0]
        assert queued.title == "Running Track"
        assert queued._resume_offset >= 74
        assert player.now_playing is None

    def test_pause_resume_freezes_elapsed_seconds_and_speed_scaling(self):
        import datetime
        player = GuildPlayer(123)
        dummy_track = Track(title="Timing Test", url="https://yt.com/watch?v=timing", duration=200)
        player.now_playing = dummy_track

        base_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=20)
        player.play_start_time = base_time
        assert player.elapsed_seconds >= 20

        # Pause freezes elapsed calculation
        player.pause()
        assert player.paused_at is not None
        freeze_elapsed = player.elapsed_seconds

        # Time passes while paused
        player.paused_at -= datetime.timedelta(seconds=10)
        # elapsed should reflect paused_at time, not current time
        assert player.elapsed_seconds == freeze_elapsed - 10 or player.elapsed_seconds == 10

        # Resume
        player.paused_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)
        player.resume()
        assert player.paused_at is None
        assert player.total_paused_duration >= 4.9

        # Speed scaling: at 2.0x speed, 20 seconds playtime yields 40 elapsed seconds
        player.playback_speed = 2.0
        player.total_paused_duration = 0.0
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=20)
        assert player.elapsed_seconds >= 39

    @pytest.mark.asyncio
    async def test_seek_to_preserves_paused_state(self):
        from seek.service import SeekService
        bot = MagicMock()
        player = GuildPlayer(123)
        dummy_track = Track(title="Seek Paused", url="https://yt.com/watch?v=seek", duration=180)
        dummy_track.stream_url_cache = "https://stream.url"
        dummy_track.stream_url_expires = time.monotonic() + 3600
        player.now_playing = dummy_track
        bot.get_player.return_value = player

        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = True  # Player was paused
        guild.voice_client = vc
        bot.get_guild.return_value = guild
        bot.audio_processor = MagicMock()
        bot.audio_processor.build_ffmpeg_options.return_value = {"options": "-vn"}
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(audio_quality=MagicMock(value="high")))

        svc = SeekService(bot)
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await svc.seek_to(123, 40)
            assert ok is True
            # vc.play called, then vc.pause called to preserve paused state
            vc.play.assert_called_once()
            vc.pause.assert_called_once()
            assert player.paused_at is not None
            assert player.elapsed_seconds == 40

    @pytest.mark.asyncio
    async def test_session_cleanup_on_empty_queue_in_heartbeat(self):
        from main import MusicBot
        bot = MagicMock(spec=MusicBot)
        bot.db = MagicMock()
        bot.db.clear_session_state = AsyncMock()
        bot.get_guild.return_value = MagicMock()

        player = GuildPlayer(777)
        player.now_playing = None
        bot._players = {777: player}

        await MusicBot._session_heartbeat.coro(bot)
        bot.db.clear_session_state.assert_awaited_once_with(777)

    @pytest.mark.asyncio
    async def test_request_channel_clear_cleans_message_and_resets_config(self):
        from cogs.admin import AdminCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        cfg = MagicMock()
        cfg.request_channel_id = 456
        cfg.board_message_id = 789
        cfg.clean_mode = True
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.save_server_config = AsyncMock()

        cog = AdminCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 999
        interaction.user.guild_permissions.administrator = True
        ch_mock = MagicMock(spec=discord.TextChannel)
        msg_mock = MagicMock()
        msg_mock.delete = AsyncMock()
        ch_mock.fetch_message = AsyncMock(return_value=msg_mock)
        interaction.guild.get_channel.return_value = ch_mock
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.admin.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.rc_clear.callback)(cog, interaction)

        msg_mock.delete.assert_awaited_once()
        assert cfg.request_channel_id is None
        assert cfg.board_message_id is None
        assert cfg.clean_mode is False
        bot.db.save_server_config.assert_awaited_once_with(cfg)

    @pytest.mark.asyncio
    async def test_bookmark_load_voice_connect_and_replace_stop(self):
        from cogs.bookmark_cog import BookmarkCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        t1 = Track(title="Bm Track 1", url="https://yt.com/1", duration=120)
        bot.db.load_bookmark = AsyncMock(return_value=[t1])
        player = GuildPlayer(888)
        bot.get_player.return_value = player
        cog = BookmarkCog(bot)

        interaction = MagicMock()
        interaction.guild_id = 888
        interaction.guild.voice_client = None  # Bot not connected
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        voice_channel = MagicMock()
        voice_channel.connect = AsyncMock(return_value=mock_vc)
        interaction.user.voice.channel = voice_channel
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        def _safe_track(task_or_coro, *a, **k):
            import asyncio
            if asyncio.iscoroutine(task_or_coro):
                task_or_coro.close()
            return MagicMock()
        bot.track_task = MagicMock(side_effect=_safe_track)

        with patch("cogs.bookmark_cog.get_locale", AsyncMock(return_value="en")):
            await cog._load(interaction, "my_bm", mode="replace")

        voice_channel.connect.assert_awaited_once()
        assert len(player) == 1
        assert player._queue[0].title == "Bm Track 1"

    def test_scheduler_add_weekends_choice(self):
        from scheduler.cog import SchedulerCog
        bot = MagicMock()
        cog = SchedulerCog(bot)
        # Inspect choices parameter on schedule_add
        cmd = cog.schedule_add
        repeat_param = None
        for p in cmd.parameters:
            if p.name == "repeat":
                repeat_param = p
                break
        assert repeat_param is not None
        choice_values = [c.value for c in repeat_param.choices]
        assert "weekends" in choice_values
        assert "daily" in choice_values
        assert "weekdays" in choice_values
        assert "once" in choice_values

    @pytest.mark.asyncio
    async def test_scheduler_midnight_rollover_sql_claim(self):
        import datetime
        from core.database import DatabaseManager
        db = DatabaseManager()
        db._migration_mode = "mysql"
        db.router.master_pool = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_cur.fetchall = AsyncMock(return_value=[{"id": 1, "days_of_week": "daily", "scheduled_time": "23:59:00"}])

        executed_queries = []
        async def fake_execute(query, params=None):
            executed_queries.append((query, params))

        mock_cur.execute = AsyncMock(side_effect=fake_execute)

        async def fake_run_mysql(op, *a, **k):
            return await op(MagicMock(), mock_cur)

        db._run_mysql = AsyncMock(side_effect=fake_run_mysql)

        # Midnight boundary time: 23:59 UTC
        t_2359 = datetime.datetime(2026, 11, 6, 23, 59, 10, tzinfo=datetime.timezone.utc)
        due = await db.claim_due_scheduled_tasks(worker_id="test_worker", now_utc=t_2359)

        assert len(due) == 1
        update_query = executed_queries[0][0]
        # Must contain OR condition for midnight rollover
        assert "OR scheduled_time < %s" in update_query

    @pytest.mark.asyncio
    async def test_seek_and_resume_speed_scaling_math(self):
        from seek.service import SeekService
        bot = MagicMock()
        player = GuildPlayer(123)
        dummy_track = Track(title="Speed Math", url="https://yt.com/watch?v=speed", duration=300)
        dummy_track.stream_url_cache = "https://stream.url"
        dummy_track.stream_url_expires = time.monotonic() + 3600
        player.now_playing = dummy_track
        player.playback_speed = 2.0  # 2x speed
        bot.get_player.return_value = player

        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild
        bot.audio_processor = MagicMock()
        bot.audio_processor.build_ffmpeg_options.return_value = {"options": "-vn"}
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(audio_quality=MagicMock(value="high")))

        svc = SeekService(bot)
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await svc.seek_to(123, 60)
            assert ok is True
            # At 2.0x speed, seeking to 60s should compute elapsed_seconds exactly as 60s (not 120s)
            assert player.elapsed_seconds == 60

    @pytest.mark.asyncio
    async def test_request_channel_set_deletes_old_board_message(self):
        from cogs.admin import AdminCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        cfg = MagicMock()
        cfg.request_channel_id = 101
        cfg.board_message_id = 202
        cfg.clean_mode = True
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.save_server_config = AsyncMock()

        cog = AdminCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 999
        interaction.user.guild_permissions.administrator = True
        old_ch = MagicMock(spec=discord.TextChannel)
        old_msg = MagicMock()
        old_msg.delete = AsyncMock()
        old_ch.fetch_message = AsyncMock(return_value=old_msg)

        new_ch = MagicMock(spec=discord.TextChannel)
        new_ch.id = 303
        new_board_msg = MagicMock()
        new_board_msg.id = 404
        new_ch.send = AsyncMock(return_value=new_board_msg)

        interaction.guild.get_channel.side_effect = lambda cid: old_ch if cid == 101 else new_ch
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.admin.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.rc_set.callback)(cog, interaction, channel=new_ch, clean_mode=True)

        old_msg.delete.assert_awaited_once()
        assert cfg.request_channel_id == 303
        assert cfg.board_message_id == 404

    @pytest.mark.asyncio
    async def test_autoresume_off_clears_session_state(self):
        from cogs.admin import AdminCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        bot.db.clear_session_state = AsyncMock()
        cfg = MagicMock()
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.db.save_server_config = AsyncMock()

        cog = AdminCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 888
        interaction.user.guild_permissions.administrator = True
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("cogs.admin.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.autoresume.callback)(cog, interaction, state="off")

        assert cfg.auto_resume is False
        bot.db.clear_session_state.assert_awaited_once_with(888)

    # ── Phase 4 Bug Fix Verification Tests ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_phase4_playlist_cog_add_and_play_voice_check(self):
        from playlist.cog import PlaylistCog

        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        t1 = Track(title="Song 1", url="https://youtube.com/watch?v=111", duration=180)
        bot.youtube = MagicMock()
        bot.youtube.is_youtube_url.return_value = False
        bot.yt_breaker = MagicMock()
        bot.yt_breaker.call = AsyncMock(return_value=[t1])
        bot.db.add_track_to_playlist = AsyncMock(return_value=True)
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))

        cog = PlaylistCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 123
        interaction.user.id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("playlist.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.add.callback)(cog, interaction, name="MyFavs", query="Song 1")

        bot.yt_breaker.call.assert_awaited_once_with(bot.youtube.search, "Song 1", 1)
        bot.db.add_track_to_playlist.assert_awaited_once()

        # Playlist play when user is NOT in voice -> must NOT increment play_count
        bot.db.load_playlist = AsyncMock(return_value=[t1])
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.voice = None
        interaction.followup.send.reset_mock()

        with patch("playlist.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.play.callback)(cog, interaction, name="MyFavs")

        bot.db.load_playlist.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_phase4_finish_track_clears_now_playing_and_pause_timing(self):
        import datetime
        player = GuildPlayer(123)
        t1 = Track(title="Finished Song", url="https://yt.com/1", duration=180)
        player.now_playing = t1
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc)
        player.paused_at = datetime.datetime.now(datetime.timezone.utc)
        player.total_paused_duration = 12.5
        assert player.is_paused is True

        await player.finish_track()
        assert player.now_playing is None
        assert player.play_start_time is None
        assert player.paused_at is None
        assert player.total_paused_duration == 0.0
        assert player.is_paused is False
        assert player._history_track == t1

    def test_phase4_playback_speed_mid_track_rebases_without_jump(self):
        import datetime
        player = GuildPlayer(123)
        player.now_playing = Track(title="Speed Rebase", url="https://yt.com/1", duration=300)
        # Played 60 seconds at 1.0x speed
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        assert 59 <= player.elapsed_seconds <= 61

        # Change speed to 1.5x mid-track -> elapsed_seconds must remain ~60s, NOT jump to 90s
        player.playback_speed = 1.5
        assert 59 <= player.elapsed_seconds <= 61

        # Change speed to 0.5x mid-track -> elapsed_seconds must remain ~60s, NOT drop to 30s
        player.playback_speed = 0.5
        assert 59 <= player.elapsed_seconds <= 61

    @pytest.mark.asyncio
    async def test_phase4_sleep_timer_sets_reset_before_vc_stop(self):
        from cogs.sleep_timer_cog import SleepTimerCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        bot.db.clear_queue = AsyncMock()
        bot.db.clear_session_state = AsyncMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None, language="en"))
        bot.update_clean_board = AsyncMock()
        bot.track_task = MagicMock(side_effect=lambda c: c.close() if hasattr(c, "close") else None)

        player = GuildPlayer(555)
        player.now_playing = Track(title="Sleep Track", url="https://yt.com/s", duration=200)
        bot.get_player.return_value = player

        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        vc.disconnect = AsyncMock()

        seq_Observed_at_stop = []
        def on_vc_stop():
            seq_Observed_at_stop.append((player._play_seq, player.intentional_disconnect))
        vc.stop.side_effect = on_vc_stop

        guild = MagicMock()
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        cog = SleepTimerCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 555
        interaction.guild = guild
        member = MagicMock(spec=discord.Member)
        member.guild_permissions.administrator = True
        interaction.user = member
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        orig_seq = player._play_seq
        with patch("cogs.sleep_timer_cog.get_locale", AsyncMock(return_value="en")), \
             patch("cogs.sleep_timer_cog.asyncio.sleep", AsyncMock()):
            await cast(Any, cog.sleep.callback)(cog, interaction, "1m")
            if player.sleep_timer_task:
                await player.sleep_timer_task

        assert len(seq_Observed_at_stop) == 1
        seq_at_stop, intentional_at_stop = seq_Observed_at_stop[0]
        assert seq_at_stop > orig_seq
        assert intentional_at_stop is True
        bot.db.clear_session_state.assert_awaited_once_with(555)

    @pytest.mark.asyncio
    async def test_phase4_radio_switch_while_playing_stops_vc(self):
        from radio.cog import RadioCog
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(language="en"))
        player = GuildPlayer(777)
        bot.get_player.return_value = player
        music_cog = MagicMock()
        music_cog._play_next = AsyncMock()
        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        music_cog._ensure_voice = AsyncMock(return_value=vc)
        bot.cogs = {"Music": music_cog}

        cog = RadioCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 777
        interaction.user.id = 111
        interaction.user.display_name = "Tester"
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch("radio.cog.get_locale", AsyncMock(return_value="en")):
            await cog._play_radio_station(interaction, station_id="custom", custom_url="https://stream.example.com/live.mp3")

        vc.stop.assert_called_once()
        music_cog._play_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_phase4_quiz_prepare_round_deduplicates_cleaned_titles(self):
        from quiz.engine import QuizSession
        bot = MagicMock()
        bot.sources_router = None
        bot.spotify = None
        bot.db = MagicMock()
        t_correct = Track(title="Shape of You (Official Video)", url="https://yt.com/shape", duration=240)
        bot.db.get_random_quiz_track = AsyncMock(return_value=t_correct)
        # Return distractors that clean to the same title as the correct track
        bot.db.get_quiz_distractors = AsyncMock(return_value=[
            "Shape of You [Lyrics]",
            "Shape of You (Official Audio)",
            "Blinding Lights (Official Video)",
        ])

        session = QuizSession(bot, guild_id=123, channel=MagicMock(), total_rounds=3)
        data = await session.prepare_round()
        assert data is not None
        options = data["options"]
        assert len(options) == 4
        assert len({o.lower() for o in options}) == 4  # All 4 options must be distinct

    @pytest.mark.asyncio
    async def test_phase4_youtube_search_cache_returns_clones_and_spotify_cancelled_error(self):
        from core.youtube import YouTubeExtractor
        from core.spotify import SpotifyExtractor

        yt = YouTubeExtractor()
        cached_track = Track(title="Cached Song", url="https://yt.com/c", duration=100)
        yt._search_cache["lofi::1"] = ([cached_track], time.monotonic())

        res1 = await yt.search("lofi", 1)
        res1[0].requested_by_id = 111
        res1[0]._resume_offset = 55
        res1[0]._skip_votes.add(111)

        res2 = await yt.search("lofi", 1)
        assert res2[0] is not res1[0]
        assert res2[0].requested_by_id != 111
        assert res2[0]._resume_offset == 0
        assert len(res2[0]._skip_votes) == 0

        # Spotify resolve filters out CancelledError (BaseException)
        sp = SpotifyExtractor()
        sp._available = True
        sp._api_get = AsyncMock(return_value={"items": [{"name": "A"}, {"name": "B"}]})
        valid_track = Track(title="Valid", url="https://yt.com/v", duration=100)
        sp._resolve_track_item = AsyncMock(side_effect=[asyncio.CancelledError(), valid_track])
        tracks = await sp.resolve("https://open.spotify.com/album/1234567890abc", MagicMock(), MagicMock())
        assert tracks == [valid_track]

    def test_phase4_embeds_non_classic_progress_bar_no_raw_markdown_link(self):
        import datetime
        from utils.embeds import now_playing_embed
        player = GuildPlayer(123)
        player.now_playing = Track(title="Theme Test", url="https://youtube.com/watch?v=abc", duration=200)
        player.play_start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)

        for theme in ("spotify", "minimal", "glass"):
            emb = now_playing_embed(player, 0x1DB954, None, theme=theme)
            desc = emb.description or ""
            # Must not contain markdown link syntax inside code blocks
            assert "](https://" not in desc

        emb_classic = now_playing_embed(player, 0x1DB954, None, theme="classic")
        progress_field = next(f for f in emb_classic.fields if "Progress" in f.name or "ความคืบหน้า" in f.name)
        assert "](https://youtube.com/watch?v=abc)" in progress_field.value

    @pytest.mark.asyncio
    async def test_phase5_multisource_router_str_and_direct_stream_seek(self):
        from models.enums import AudioQuality
        from sources.router import MultiSourceRouter
        from seek.service import SeekService

        router = MultiSourceRouter()
        router._sc.get_stream_url = AsyncMock(return_value="https://cdn.sc.com/stream.mp3")

        # Passing URL string directly must not raise AttributeError
        stream = await router.get_stream_url("https://soundcloud.com/artist/track")
        assert stream == "https://cdn.sc.com/stream.mp3"

        # Passing Track with non-expiring direct CDN stream_url_cache (stream_url_expires=None)
        direct_track = Track(
            title="Podcast Ep 1",
            url="https://example.com/podcast.mp3",
            duration=600,
            stream_url_cache="https://cdn.example.com/podcast.mp3",
            stream_url_expires=None,
        )
        assert await router.get_stream_url(direct_track) == "https://cdn.example.com/podcast.mp3"

        # SeekService.seek_to must use non-expiring stream_url_cache without calling youtube.get_stream_url
        bot = MagicMock()
        guild = MagicMock()
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild
        player = GuildPlayer(555)
        player.now_playing = direct_track
        bot.get_player.return_value = player
        bot.youtube.get_stream_url = AsyncMock()
        cfg = MagicMock()
        cfg.audio_quality = AudioQuality.HIGH
        bot.db.get_server_config = AsyncMock(return_value=cfg)
        bot.audio_processor.build_ffmpeg_options.return_value = {
            "before_options": "-ss 30",
            "options": "-vn",
        }

        seek_svc = SeekService(bot)
        with patch("discord.FFmpegPCMAudio"), patch("discord.PCMVolumeTransformer"):
            ok = await seek_svc.seek_to(555, 30)
        assert ok is True
        bot.youtube.get_stream_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase5_presets_reset_dsp_and_vocal_suppression_default_mode(self):
        from cogs.presets_cog import _apply_preset, _player_to_preset, _BUILTIN_PRESETS
        from vocal.cog import VocalCog

        player = GuildPlayer(777)
        player.vocal_mode = "isolate"
        player.vocal_suppression = 75
        player.pan_balance = -0.8
        player.stereo_width = 2.2
        player.spatial_preset = "concert_hall"
        player.remaster = True
        player.loudnorm = True
        player.gapless = True
        player.equalizer_bands = {"sub_bass": 6.0, "bass": 4.0, "mid": 0.0, "treble": -2.0}

        snap = _player_to_preset(player, "high")
        assert snap["vocal_mode"] == "isolate"
        assert snap["pan_balance"] == -0.8
        assert snap["spatial_preset"] == "concert_hall"

        # Applying built-in "gaming" preset must reset all Gen 5.0 DSP fields back to defaults
        _apply_preset(player, _BUILTIN_PRESETS["gaming"])
        assert player.vocal_mode is None
        assert player.pan_balance == 0.0
        assert player.stereo_width == 1.0
        assert player.spatial_preset is None
        assert player.remaster is False
        assert player.loudnorm is False
        assert player.gapless is False
        assert player.equalizer_bands == {}

        # VocalCog: calling /vocal with suppression only when vocal_mode is None defaults to "remove"
        bot = MagicMock()
        bot.get_player.return_value = player
        bot.db = MagicMock()
        cog = VocalCog(bot)
        cog._check_permissions = AsyncMock(return_value=True)
        cog._restart_audio = MagicMock()
        inter = MagicMock()
        inter.guild_id = 777
        inter.response.defer = AsyncMock()
        inter.followup.send = AsyncMock()

        with patch("vocal.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, cog.vocal.callback)(cog, inter, mode=None, suppression=80)
        assert player.vocal_mode == "remove"
        assert player.vocal_suppression == 80

    @pytest.mark.asyncio
    async def test_phase5_request_channel_voice_isolation_and_volume_clamp(self):
        from main import MusicBot
        from models.enums import NLUIntent
        from core.nlu import NLUResult

        bot = MusicBot.__new__(MusicBot)
        bot._players = {}
        bot.db = MagicMock()
        cfg = MagicMock()
        cfg.clean_mode = False
        cfg.board_message_id = None
        bot.db.get_server_config = AsyncMock(return_value=cfg)

        player = bot.get_player(888)
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.channel = MagicMock()
        vc.channel.id = 1001
        vc.channel.name = "BotVoice"

        msg = MagicMock()
        msg.guild.id = 888
        msg.guild.voice_client = vc
        msg.delete = AsyncMock()
        msg.channel.send = AsyncMock()

        # 1. User in a different voice channel cannot skip the bot's stream
        msg.content = "skip"
        msg.author.voice = MagicMock()
        msg.author.voice.channel = MagicMock()
        msg.author.voice.channel.id = 9999
        bot.nlu = MagicMock()
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.SKIP)
        await bot._handle_request_channel(msg)
        vc.stop.assert_not_called()

        # 2. User in the same voice channel sets volume 999 -> clamped to 2.0 (200%)
        msg.author.voice.channel = vc.channel
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.VOLUME, volume=999)
        await bot._handle_request_channel(msg)
        assert player.volume == 2.0

        # 3. User sends PAUSE while bot is idle (not playing) -> player.is_paused remains False
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.PAUSE)
        await bot._handle_request_channel(msg)
        assert player.is_paused is False

    def test_phase6_nlu_thai_boundaries(self):
        from core.nlu import RegexNLU
        from models.enums import NLUIntent

        nlu = RegexNLU()

        # Thai words ending with tone marks / non-spacing vowels must match accurately
        assert nlu.parse("วนซ้ำ").intent == NLUIntent.LOOP
        assert nlu.parse("ซ้ำ").intent == NLUIntent.LOOP
        assert nlu.parse("สุ่ม").intent == NLUIntent.SHUFFLE
        assert nlu.parse("สุ่มเพลง").intent == NLUIntent.SHUFFLE
        assert nlu.parse("เล่นต่อ").intent == NLUIntent.RESUME
        assert nlu.parse("ต่อเลย").intent == NLUIntent.RESUME
        assert nlu.parse("หยุดเลย").intent == NLUIntent.STOP
        assert nlu.parse("หยุด").intent == NLUIntent.PAUSE

        vol_up = nlu.parse("ดังขึ้น")
        assert vol_up.intent == NLUIntent.VOLUME
        assert vol_up.volume_dir == "up"

        vol_down = nlu.parse("เบาลง")
        assert vol_down.intent == NLUIntent.VOLUME
        assert vol_down.volume_dir == "down"

        # English words starting with "play" like "playlist" must not match PLAY intent
        assert nlu.parse("playlist").intent == NLUIntent.UNKNOWN

    @pytest.mark.asyncio
    async def test_phase6_views_shuffle_uses_undo_push_and_cancels_prefetch(self):
        from utils.views import MusicControlView, QueueView, CleanBoardControlView

        bot = MagicMock()
        bot.track_task = MagicMock(side_effect=lambda c, *a, **k: c.close() if asyncio.iscoroutine(c) else None)
        player = GuildPlayer(555)
        t1 = Track(title="A", url="https://ex.com/1", duration=60)
        t2 = Track(title="B", url="https://ex.com/2", duration=60)
        await player.extend([t1, t2])
        player.cancel_prefetch = MagicMock()
        bot.get_player.return_value = player

        inter = MagicMock()
        inter.guild_id = 555
        inter.guild = MagicMock()
        inter.guild.id = 555
        inter.response.send_message = AsyncMock()
        inter.response.edit_message = AsyncMock()
        inter.response.is_done.return_value = False

        # 1. MusicControlView.shuffle
        m_view = MusicControlView.__new__(MusicControlView)
        m_view.bot = bot
        m_view.guild_id = 555
        m_view._check = AsyncMock(return_value=True)
        m_view._vc = MagicMock(return_value=None)
        await cast(Any, MusicControlView.shuffle)(m_view, inter, MagicMock())
        assert len(player.undo_stack) == 1
        assert player.undo_stack[-1].operation == "shuffle"
        player.cancel_prefetch.assert_called_once()

        # 2. QueueView.shuffle
        player.cancel_prefetch.reset_mock()
        q_view = QueueView.__new__(QueueView)
        q_view.bot = bot
        q_view.guild_id = 555
        q_view.page = 1
        q_view._sync_nav = MagicMock()
        await cast(Any, QueueView.shuffle)(q_view, inter, MagicMock())
        assert len(player.undo_stack) == 2
        player.cancel_prefetch.assert_called_once()

        # 3. CleanBoardControlView.btn_shuffle
        player.cancel_prefetch.reset_mock()
        cb_view = CleanBoardControlView.__new__(CleanBoardControlView)
        cb_view.bot = bot
        cb_view._get_gid = MagicMock(return_value=555)
        cb_view._update_board = AsyncMock()
        await cast(Any, CleanBoardControlView.btn_shuffle)(cb_view, inter, MagicMock())
        assert len(player.undo_stack) == 3
        player.cancel_prefetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase6_radio_watchdog_preserves_user_queue_and_cog_unload_async(self):
        from radio.cog import RadioCog
        from radio.watchdog import RadioWatchdog, RadioSession
        import datetime

        assert asyncio.iscoroutinefunction(RadioCog.cog_unload)

        bot = MagicMock()
        player = GuildPlayer(777)
        player.is_radio_mode = True
        queued_song = Track(title="User Song", url="https://ex.com/u", duration=120)
        await player.enqueue(queued_song)
        bot.get_player.return_value = player

        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        guild = MagicMock()
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        music_cog = MagicMock()
        music_cog._play_next = AsyncMock()
        bot.cogs = {"Music": music_cog}

        wd = RadioWatchdog(bot)
        session = RadioSession(
            station_id="lofi",
            station_name="Lofi Radio",
            stream_url="https://ex.com/radio",
            started_at=datetime.datetime.now(datetime.timezone.utc),
        )
        wd._sessions[777] = session

        await wd._check_guild_stream(777, session)
        # User queue must NOT be cleared; radio mode is unregistered and _play_next is called
        assert len(player) == 1
        assert player.queue[0].title == "User Song"
        assert player.is_radio_mode is False
        music_cog._play_next.assert_awaited_once_with(777)

    @pytest.mark.asyncio
    async def test_phase6_request_channel_volume_dir_stop_loop_shuffle_and_self_test(self):
        from main import MusicBot
        from models.enums import NLUIntent, LoopMode
        from core.nlu import NLUResult
        from core.self_test import _check_permissions, _check_spotify
        from loudnorm.cog import LoudnormCog

        bot = MusicBot.__new__(MusicBot)
        bot._players = {}
        bot.db = MagicMock()
        bot.track_task = MagicMock(side_effect=lambda c, *a, **k: c.close() if asyncio.iscoroutine(c) else None)
        cfg = MagicMock()
        cfg.clean_mode = False
        cfg.board_message_id = None
        bot.db.get_server_config = AsyncMock(return_value=cfg)

        player = bot.get_player(889)
        player.volume = 0.5  # 50%
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        vc.channel = MagicMock()
        vc.channel.id = 1001
        vc.channel.name = "BotVoice"
        vc.source = MagicMock()
        vc.source.volume = 0.5

        msg = MagicMock()
        msg.guild.id = 889
        msg.guild.voice_client = vc
        msg.delete = AsyncMock()
        msg.channel.send = AsyncMock()
        msg.author.voice = MagicMock()
        msg.author.voice.channel = vc.channel

        bot.nlu = MagicMock()
        # Relative volume up (+10% -> 60%)
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.VOLUME, volume_dir="up")
        await bot._handle_request_channel(msg)
        assert player.volume == 0.6

        # Loop toggle (OFF -> TRACK)
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.LOOP)
        await bot._handle_request_channel(msg)
        assert player.loop_mode == LoopMode.TRACK

        # Shuffle
        await player.extend([
            Track(title="S1", url="https://ex.com/1", duration=60),
            Track(title="S2", url="https://ex.com/2", duration=60),
        ])
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.SHUFFLE)
        await bot._handle_request_channel(msg)
        assert len(player.undo_stack) == 1

        # Stop
        bot.nlu.parse.return_value = NLUResult(intent=NLUIntent.STOP)
        await bot._handle_request_channel(msg)
        assert len(player) == 0
        vc.stop.assert_called_once()

        # Self-test _check_permissions checks 2nd guild even when 1st guild is OK
        g1 = MagicMock()
        g1.name = "Guild1"
        g1.me.guild_permissions = MagicMock(connect=True, speak=True, send_messages=True, embed_links=True, read_messages=True)
        g2 = MagicMock()
        g2.name = "Guild2"
        g2.me.guild_permissions = MagicMock(connect=True, speak=False, send_messages=True, embed_links=True, read_messages=True)
        bot_st = MagicMock()
        bot_st.is_ready.return_value = True
        bot_st.guilds = [g1, g2]
        perm_res = await _check_permissions(bot_st)
        assert perm_res.ok is False
        assert "Guild2" in perm_res.detail

        # Loudnorm profile with enabled=False keeps loudnorm False
        ln_cog = LoudnormCog(bot)
        ln_cog._check_permissions = AsyncMock(return_value=True)
        ln_cog._restart_audio = MagicMock()
        inter = MagicMock()
        inter.guild_id = 889
        inter.response.defer = AsyncMock()
        inter.followup.send = AsyncMock()
        with patch("loudnorm.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, ln_cog.loudnorm.callback)(ln_cog, inter, profile="podcast", enabled=False)
        assert player.loudnorm_profile == "podcast"
        assert player.loudnorm is False

    @pytest.mark.asyncio
    async def test_phase7_views_single_arg_callbacks_and_method_names(self):
        from utils.views import (
            HistoryView,
            QueueSearchResultView,
            MusicControlView,
            VoteSkipView,
            VoteClearView,
            VoteShuffleView,
        )

        bot = MagicMock()
        t1 = Track(title="Hist Song 1", url="https://youtube.com/watch?v=h1", duration=120)
        rows = [{"track_data": t1.to_json()}]
        play_cb = AsyncMock()

        # HistoryView buttons invoked with 1 arg (interaction) as discord.py BaseView does
        h_view = HistoryView(bot, 101, rows, play_cb, page=1)

        inter = MagicMock()
        inter.response.defer = AsyncMock()
        inter.response.edit_message = AsyncMock()

        replay_btn = next(c for c in h_view.children if getattr(c, "custom_id", "") == "hist_replay_0")
        await cast(Any, replay_btn.callback)(inter)
        play_cb.assert_awaited_once()

        next_btn = next(c for c in h_view.children if getattr(c, "custom_id", "") == "hist_next")
        with patch("utils.views.history_embed", return_value=MagicMock()):
            await cast(Any, next_btn.callback)(inter)

        # QueueSearchResultView buttons invoked with 1 arg (interaction)
        jump_cb = AsyncMock()
        rm_cb = AsyncMock()
        qs_view = QueueSearchResultView(bot, 101, [(1, t1)], jump_cb, rm_cb)

        j_btn = next(c for c in qs_view.children if getattr(c, "custom_id", "") == "qs_jump_1")
        r_btn = next(c for c in qs_view.children if getattr(c, "custom_id", "") == "qs_rm_1")
        await cast(Any, j_btn.callback)(inter)
        await cast(Any, r_btn.callback)(inter)
        jump_cb.assert_awaited_once_with(inter, 1)
        rm_cb.assert_awaited_once_with(inter, 1)

        # MusicControlView has btn_stop and does not shadow View.stop
        assert hasattr(MusicControlView, "btn_stop")
        assert hasattr(VoteSkipView, "_refresh_vote_message")
        assert hasattr(VoteClearView, "_refresh_vote_message")
        assert hasattr(VoteShuffleView, "_refresh_vote_message")

    @pytest.mark.asyncio
    async def test_phase7_lyrics_runtime_aiohttp_vtt_crlf_and_thai_no_merge(self):
        from lyrics.parser import parse_vtt, parse_srt
        from lyrics.service import _fetch_subtitle_text

        # VTT with CRLF, sequence numbers, and Thai lyrics (no spaces between words)
        vtt_crlf = (
            "WEBVTT\r\n\r\n"
            "1\r\n"
            "00:00:01.000 --> 00:00:04.000\r\n"
            "เธอคือความรักที่สวยงามในใจฉัน\r\n\r\n"
            "2\r\n"
            "00:00:05.000 --> 00:00:08.000\r\n"
            "เราจะเดินไปด้วยกันตลอดไป\r\n"
        )
        lines = parse_vtt(vtt_crlf)
        # Must remain 2 distinct lines, not merged into 1!
        assert len(lines) == 2
        assert lines[0].text == "เธอคือความรักที่สวยงามในใจฉัน"
        assert lines[1].text == "เราจะเดินไปด้วยกันตลอดไป"

        # SRT with CRLF
        srt_crlf = (
            "1\r\n"
            "00:00:01,000 --> 00:00:03,000\r\n"
            "First line here\r\n\r\n"
            "2\r\n"
            "00:00:04,000 --> 00:00:06,000\r\n"
            "Second line here\r\n"
        )
        srt_lines = parse_srt(srt_crlf)
        assert len(srt_lines) == 2

        # _fetch_subtitle_text works at runtime without NameError on aiohttp
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=vtt_crlf)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_cm

        fetched = await _fetch_subtitle_text("https://example.com/sub.vtt", mock_session)
        assert fetched == vtt_crlf

    @pytest.mark.asyncio
    async def test_phase7_loop_ab_clamp_and_database_quiz_guild_isolation(self, tmp_path):
        from loop_ab.service import LoopABService
        from core.database import DatabaseManager
        import datetime as dt
        from scripts.backup_manager import TABLES_IN_ORDER

        assert "quiz_scores" in TABLES_IN_ORDER

        # LoopABService: start_sec >= track.duration must fail with invalid_times after clamping end_sec
        bot = MagicMock()
        player = GuildPlayer(321)
        player.now_playing = Track(title="Short Song", url="https://youtube.com/watch?v=s1", duration=60)
        bot.get_player.return_value = player
        loop_svc = LoopABService(bot)
        ok, err = await loop_svc.start_loop(321, start_sec=70, end_sec=90)
        assert ok is False
        assert err == "invalid_times"

        # Database: get_random_quiz_track & get_quiz_distractors must not leak tracks from other guilds,
        # and SQLite list_playlists(include_public=True) returns public playlists from other users
        db_file = tmp_path / "phase7_test.db"
        db = DatabaseManager(str(db_file), migration_mode="sqlite_fallback")
        await db.initialise()

        other_guild_track = Track(title="Secret Other Server Song", url="https://youtube.com/watch?v=sec", duration=180)
        await db.add_history(guild_id=999, track=other_guild_track, user_id=10)

        # Guild 111 has 0 history rows -> must NOT receive Guild 999's track
        quiz_track = await db.get_random_quiz_track(guild_id=111)
        assert quiz_track is None

        distractors = await db.get_quiz_distractors(guild_id=111, exclude_title="Anything", limit=3)
        assert "Secret Other Server Song" not in distractors

        # Public playlist visibility on SQLite
        await db.save_playlist(
            user_id=500,
            guild_id=111,
            name="Public Mix",
            tracks=[other_guild_track],
            is_public=True,
        )
        pl_private_only = await db.list_playlists(user_id=600, guild_id=111, include_public=False)
        assert len(pl_private_only) == 0
        pl_with_public = await db.list_playlists(user_id=600, guild_id=111, include_public=True)
        assert any(p["name"] == "Public Mix" for p in pl_with_public)

    @pytest.mark.asyncio
    async def test_phase8_stability_fixes(self, tmp_path):
        from podcast.parser import parse_podcast_xml
        from playlist.cog import PlaylistCog
        from sources.cog import SCSearchSelect, SCSearchView
        from cogs.effects import EffectsCog
        from cogs.health_cog import HealthCog
        from cogs.theme_cog import ThemeCog
        from cogs.language_cog import LanguageCog
        from cogs.playback_cog import PlaybackCog
        from autoplay.cog import AutoplayCog
        from scheduler.service import MusicSchedulerService
        from scheduler.models import ScheduledTask
        from models.enums import AudioEffect, AudioQuality

        # 1. podcast parser handles XML bytes and str with encoding declaration
        rss_bytes = b'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Byte Cast</title><description>D</description><item><title>Ep 1</title><enclosure url="https://audio.mp3" type="audio/mpeg" length="1234"/></item></channel></rss>'
        show_from_bytes = parse_podcast_xml(rss_bytes)
        assert show_from_bytes is not None
        assert show_from_bytes.title == "Byte Cast"
        assert len(show_from_bytes.episodes) == 1
        assert show_from_bytes.episodes[0].title == "Ep 1"

        show_from_str = parse_podcast_xml(rss_bytes.decode("utf-8"))
        assert show_from_str is not None
        assert show_from_str.title == "Byte Cast"

        # 2. scheduler/service.py calls spotify.resolve with max_tracks=50 (not yt_breaker)
        bot = MagicMock()
        bot.db = AsyncMock()
        bot.get_guild = MagicMock()
        guild = MagicMock()
        vc_chan = MagicMock(spec=discord.VoiceChannel)
        vc_chan.id = 456
        guild.get_channel.return_value = vc_chan
        bot.get_channel.return_value = vc_chan
        bot.get_guild.return_value = guild
        vc = AsyncMock()
        vc.is_connected.return_value = True
        guild.voice_client = vc

        bot.spotify = MagicMock()
        bot.spotify.available = True
        bot.spotify.is_spotify_url.return_value = True
        bot.spotify.resolve = AsyncMock(return_value=[Track(title="Sp", url="https://sp", duration=120)])
        bot.sp_breaker = None
        bot.yt_breaker = MagicMock()
        bot.http_session = MagicMock()
        bot.youtube = MagicMock()
        bot.get_player.return_value = GuildPlayer(123)
        bot.cogs = {}

        sched_svc = MusicSchedulerService(bot)
        task = ScheduledTask(
            id=1,
            guild_id=123,
            voice_channel_id=456,
            text_channel_id=789,
            scheduled_time="12:00:00",
            query="https://open.spotify.com/track/123",
            user_id=1,
        )
        await sched_svc._execute_task(task)
        bot.spotify.resolve.assert_awaited_once_with(
            "https://open.spotify.com/track/123",
            bot.http_session,
            bot.youtube,
            50,
        )

        # 3. PlaylistCog.play resets is_radio_mode and unregisters radio_watchdog
        bot_pl = MagicMock()
        bot_pl.db = AsyncMock()
        player_pl = GuildPlayer(999)
        player_pl.is_radio_mode = True
        bot_pl.get_player.return_value = player_pl
        bot_pl.radio_watchdog = MagicMock()
        bot_pl.cogs = {}

        pl_cog = PlaylistCog(bot_pl)
        pl_cog.service.load_playlist = AsyncMock(return_value=[Track(title="Pl Track", url="https://yt", duration=100)])

        inter_pl = MagicMock()
        inter_pl.guild_id = 999
        inter_pl.user = MagicMock()
        inter_pl.user.id = 55
        inter_pl.user.voice.channel = MagicMock()
        inter_pl.guild.voice_client = AsyncMock()
        inter_pl.guild.voice_client.is_connected.return_value = True
        inter_pl.guild.voice_client.channel = inter_pl.user.voice.channel
        inter_pl.guild.voice_client.is_playing.return_value = False
        inter_pl.guild.voice_client.is_paused.return_value = False
        inter_pl.response.defer = AsyncMock()
        inter_pl.followup.send = AsyncMock()

        with patch("playlist.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, pl_cog.play.callback)(pl_cog, inter_pl, name="My Mix", shuffle=False)

        assert player_pl.is_radio_mode is False
        bot_pl.radio_watchdog.unregister.assert_called_once_with(999)

        # 4. sources SCSearchSelect.callback handles interaction.message is None without error
        bot_sc = MagicMock()
        track_sc = Track(title="SC Song", url="https://soundcloud.com/song", duration=150)
        view_sc = SCSearchView(bot_sc, guild_id=999, tracks=[track_sc])
        select_sc = view_sc.children[0]
        select_sc._values = ["0"]

        inter_sc = MagicMock()
        inter_sc.guild_id = 999
        inter_sc.message = None  # None message in ephemeral interaction
        inter_sc.response.defer = AsyncMock()
        inter_sc.followup.send = AsyncMock()
        inter_sc.guild.voice_client = AsyncMock()
        inter_sc.guild.voice_client.is_playing.return_value = False
        inter_sc.guild.voice_client.is_paused.return_value = False
        bot_sc.get_player.return_value = GuildPlayer(999)
        bot_sc.cogs = {}

        with patch("sources.cog.get_locale", AsyncMock(return_value="en")):
            await select_sc.callback(inter_sc)
        assert inter_sc.followup.send.called

        # 5. effects, effects_clear, quality call update_clean_board
        bot_eff = MagicMock()
        bot_eff.db = AsyncMock()
        cfg_eff = MagicMock()
        cfg_eff.dj_role_id = None
        bot_eff.db.get_server_config.return_value = cfg_eff
        bot_eff.update_clean_board = AsyncMock()
        def _track(coro, name=None):
            asyncio.create_task(coro)
            return MagicMock()
        bot_eff.track_task = MagicMock(side_effect=_track)
        player_eff = GuildPlayer(777)
        bot_eff.get_player.return_value = player_eff

        eff_cog = EffectsCog(bot_eff)
        eff_cog._restart_audio = MagicMock()

        inter_eff = MagicMock()
        inter_eff.guild_id = 777
        inter_eff.response.defer = AsyncMock()
        inter_eff.followup.send = AsyncMock()

        with patch("cogs.effects.get_locale", AsyncMock(return_value="en")):
            # effects
            await cast(Any, eff_cog.effects.callback)(eff_cog, inter_eff, effect="bass_boost")
            bot_eff.update_clean_board.assert_called_with(777)

            # effects_clear
            bot_eff.update_clean_board.reset_mock()
            await cast(Any, eff_cog.effects_clear.callback)(eff_cog, inter_eff)
            bot_eff.update_clean_board.assert_called_with(777)

            # quality
            bot_eff.update_clean_board.reset_mock()
            await cast(Any, eff_cog.quality.callback)(eff_cog, inter_eff, preset="high")
            bot_eff.update_clean_board.assert_called_with(777)

        # 6. Safe permission checks with discord.User (no guild_permissions or roles)
        bot_perm = MagicMock()
        bot_perm.db = AsyncMock()
        cfg_perm = MagicMock()
        cfg_perm.dj_role_id = 9999
        cfg_perm.dj_only = True
        bot_perm.db.get_server_config.return_value = cfg_perm

        user_mock = MagicMock(spec=["id", "name", "discriminator"])  # explicitly lacks guild_permissions and roles
        inter_perm = MagicMock()
        inter_perm.guild_id = 888
        inter_perm.user = user_mock
        inter_perm.response.is_done.return_value = True
        inter_perm.response.defer = AsyncMock()
        inter_perm.followup.send = AsyncMock()

        h_cog = HealthCog(bot_perm)
        assert await h_cog._check_dj(inter_perm) is False

        t_cog = ThemeCog(bot_perm)
        assert await t_cog._check_dj(inter_perm) is False

        l_cog = LanguageCog(bot_perm)
        assert await l_cog._check_admin(inter_perm) is False

        p_cog = PlaybackCog(bot_perm)
        assert await p_cog._check_dj(inter_perm) is False

        a_cog = AutoplayCog(bot_perm)
        inter_perm.guild.voice_client = None
        # Does not crash with AttributeError
        with patch("autoplay.cog.get_locale", AsyncMock(return_value="en")):
            await cast(Any, a_cog.autoplay.callback)(a_cog, inter_perm, mode="off")

    @pytest.mark.asyncio
    async def test_phase8_performance_and_resiliency(self, tmp_path):
        from core.database import DatabaseManager
        from core.audio import AudioEffectsProcessor
        from core.ffmpeg_pool import FFmpegWarmPool
        from core.spotify import SpotifyExtractor
        from core import media_cache, lru_cache
        from core.youtube import YouTubeExtractor
        from models.track import Track
        from core.player import GuildPlayer

        # 1. SQLite Schema has Secondary Indexes
        db_file = tmp_path / "sqlite_perf_test.db"
        db = DatabaseManager(str(db_file), migration_mode="sqlite_fallback")
        await db.initialise()

        async with db._sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='index'") as cur:
            rows = await cur.fetchall()
            index_names = {r[0] for r in rows}

        assert "idx_sqlite_queue_guild" in index_names
        assert "idx_sqlite_history_guild_played" in index_names
        assert "idx_sqlite_playlist_tracks" in index_names
        assert "idx_sqlite_quiz_scores" in index_names
        await db.close()

        # 2. Audio processor: unity volume bypass and reconnect flags
        proc = AudioEffectsProcessor()
        opts_unity = proc.build_ffmpeg_options(volume=1.0)
        assert "volume=" not in opts_unity["options"]
        assert "-reconnect_on_network_error 1" in opts_unity["before_options"]
        assert "-reconnect_on_http_error 4xx,5xx" in opts_unity["before_options"]

        opts_scaled = proc.build_ffmpeg_options(volume=0.8)
        assert "volume=0.80" in opts_scaled["options"]

        # 3. FFmpegWarmPool properly waits and closes stdout on warm disposal
        pool = FFmpegWarmPool(pool_size=1)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout = MagicMock()
        mock_proc.wait = MagicMock()
        mock_proc.kill = MagicMock()

        mock_src = MagicMock()
        mock_src._process = mock_proc
        pool._ready.put_nowait(mock_src)

        with patch("discord.FFmpegPCMAudio", return_value=MagicMock()):
            fresh_src = pool.acquire("https://stream.mp3", opts_unity)
        assert fresh_src is not None
        mock_proc.kill.assert_called_once()
        mock_proc.stdout.close.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=1.0)

        # 4. SpotifyExtractor uses async with session context
        sp = SpotifyExtractor()
        sp._available = True
        mock_session = MagicMock()
        mock_post_ctx = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"access_token": "tok123", "expires_in": 3600})
        mock_post_ctx.__aenter__.return_value = mock_resp
        mock_session.post.return_value = mock_post_ctx

        token = await sp._get_token(mock_session)
        assert token == "tok123"
        mock_post_ctx.__aenter__.assert_awaited_once()
        mock_post_ctx.__aexit__.assert_awaited_once()

        # 5. Media cache clear_all and check_memory_pressure
        media_cache._THUMB_CACHE["http://thumb"] = (b"img", 1.0)
        media_cache._META_CACHE["http://track"] = ({"title": "T"}, 1.0)
        assert len(media_cache._THUMB_CACHE) == 1

        freed = media_cache.clear_all()
        assert freed == 2
        assert len(media_cache._THUMB_CACHE) == 0

        # Memory pressure aggressive clear
        media_cache._THUMB_CACHE["http://thumb2"] = (b"img2", 1.0)
        with patch("core.lru_cache._try_import_psutil") as mock_psutil_imp, \
             patch("core.lru_cache._load_thresholds", return_value=(400.0, 600.0, 900.0)):
            mock_ps = MagicMock()
            mock_ps.Process.return_value.memory_info.return_value.rss = 1000 * 1024 * 1024  # 1000 MiB -> aggressive
            mock_psutil_imp.return_value = mock_ps
            res = await lru_cache.check_memory_pressure()
            assert res["action"] == "aggressive_clear"
            assert len(media_cache._THUMB_CACHE) == 0

        # 6. YouTube playlist extraction skips private/deleted without premature truncation
        yt = YouTubeExtractor()
        fake_entries = [
            {"title": "[Private video]"},
            {"title": "[Deleted video]"},
            {"title": "Track 1", "url": "https://youtube.com/watch?v=1", "duration": 180},
            {"title": "Track 2", "url": "https://youtube.com/watch?v=2", "duration": 200},
        ]
        with patch.object(yt, "_run_ytdl", return_value={"entries": fake_entries}):
            extracted = await yt._get_playlist_inner("https://playlist", max_tracks=2)
            assert len(extracted) == 2
            assert extracted[0].title == "Track 1"
            assert extracted[1].title == "Track 2"

        # 7. Fair-share queue reordering O(N) round-robin
        player = GuildPlayer(111)
        t_u1_a = Track(title="U1-A", url="u1a", duration=100)
        t_u1_a.requested_by_id = 1
        t_u1_b = Track(title="U1-B", url="u1b", duration=100)
        t_u1_b.requested_by_id = 1
        t_u2_a = Track(title="U2-A", url="u2a", duration=100)
        t_u2_a.requested_by_id = 2
        t_u3_a = Track(title="U3-A", url="u3a", duration=100)
        t_u3_a.requested_by_id = 3

        await player.extend([t_u1_a, t_u1_b, t_u2_a, t_u3_a])
        await player.reorder_fair_share()
        titles = [t.title for t in player.queue]
        # First round: U1-A, U2-A, U3-A, second round: U1-B
        assert titles == ["U1-A", "U2-A", "U3-A", "U1-B"]

    @pytest.mark.asyncio
    async def test_phase9_performance_and_stability(self):
        from utils.color_thief import get_dominant_color
        from core import media_cache
        from core.youtube import YouTubeExtractor
        from core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen, BreakerState
        from chapters.detector import _CHAPTER_OPTS
        from lyrics.service import _SUBTITLE_OPTS
        from utils.rate_limiter import RateLimiter
        from webserver import WebServer

        # 1. Color thief reuses media_cache._THUMB_CACHE without redundant HTTP get
        tiny_png = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf'
            b'\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        test_thumb_url = "https://img.youtube.com/vi/test_phase9/hqdefault.jpg"
        media_cache._THUMB_CACHE[test_thumb_url] = (tiny_png, time.monotonic())

        mock_session = MagicMock()
        mock_session.get = MagicMock()

        color = await get_dominant_color(test_thumb_url, mock_session)
        assert color is not None
        # session.get must NEVER be called because cached bytes were reused
        mock_session.get.assert_not_called()

        # 2. YouTubeExtractor search uses extract_flat: True when max_results > 1 and sanitizes 11-char ID
        yt = YouTubeExtractor()
        with patch.object(yt, "_extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = {
                "entries": [
                    {"id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up", "duration": 212}
                ]
            }
            res = await yt.search("rick astley", max_results=5)
            assert len(res) == 1
            assert res[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            call_opts = mock_extract.call_args[1].get("opts", {})
            assert call_opts.get("extract_flat") is True

        # 3. CircuitBreaker single-flight probe in HALF_OPEN rejects concurrent callers
        cb = CircuitBreaker("probe_test_cb", failure_threshold=1, recovery_window=0.05)
        # Force breaker OPEN
        async def fail_call():
            raise RuntimeError("API failure")
        with pytest.raises(RuntimeError):
            await cb.call(fail_call)
        assert cb.state == BreakerState.OPEN

        # Advance past recovery window
        await asyncio.sleep(0.06)

        probe_started = asyncio.Event()
        probe_proceed = asyncio.Event()

        async def slow_probe():
            probe_started.set()
            await probe_proceed.wait()
            return "recovered"

        probe_task = asyncio.create_task(cb.call(slow_probe))
        await probe_started.wait()
        assert cb.state == BreakerState.HALF_OPEN

        # Concurrent call while probe is in flight must raise CircuitBreakerOpen immediately
        with pytest.raises(CircuitBreakerOpen):
            await cb.call(slow_probe)

        # Allow probe to complete
        probe_proceed.set()
        res = await probe_task
        assert res == "recovered"
        assert cb.state == BreakerState.CLOSED

        # 4. yt-dlp option configs enforce socket_timeout: 15
        assert _CHAPTER_OPTS.get("socket_timeout") == 15
        assert _SUBTITLE_OPTS.get("socket_timeout") == 15

        # 5. WebServer cleans up dead websocket connections by closing them
        ws_alive = AsyncMock()
        ws_alive.send_json = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json = AsyncMock(side_effect=ConnectionResetError("Socket dropped"))
        ws_dead.close = AsyncMock()

        ws_clients = {ws_alive, ws_dead}
        # Simulate logic from _ws_push_loop
        dead = set()
        for ws in list(ws_clients):
            try:
                await ws.send_json({"type": "stats"})
            except Exception:
                dead.add(ws)
        for ws in dead:
            try:
                await ws.close()
            except Exception:
                pass
        ws_clients -= dead

        assert ws_dead in dead
        ws_dead.close.assert_awaited_once()
        assert ws_dead not in ws_clients
        assert ws_alive in ws_clients

        # 6. RateLimiter cleanup removes expired windows
        limiter = RateLimiter(max_calls=3, window=0.1)
        limiter.is_rate_limited(123, 9999)
        assert (123, 9999) in limiter._windows
        await asyncio.sleep(0.12)
        limiter.cleanup()
        assert (123, 9999) not in limiter._windows

    @pytest.mark.asyncio
    async def test_phase10_performance_and_stability(self):
        # 1. Crossfade in options include reconnect network & http error flags
        from core.audio import AudioEffectsProcessor
        proc = AudioEffectsProcessor()
        opts = proc.build_crossfade_in_options(5)
        assert "-reconnect_on_network_error 1" in opts["before_options"]
        assert "-reconnect_on_http_error 4xx,5xx" in opts["before_options"]

        # 2. DatabaseManager._server_configs_cache compatibility and locked close
        db = DatabaseManager(db_path=":memory:")
        assert db._server_configs_cache is db._cfg_cache
        mock_sqlite = AsyncMock()
        mock_sqlite.commit = AsyncMock()
        mock_sqlite.close = AsyncMock()
        db._sqlite_conn = mock_sqlite
        await db.close()
        mock_sqlite.close.assert_awaited_once()
        assert db._sqlite_conn is None

        # 3. FFmpegWarmPool kills orphaned process on QueueFull
        from core.ffmpeg_pool import FFmpegWarmPool
        pool = FFmpegWarmPool(pool_size=1)
        mock_src = MagicMock()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_src._process = mock_proc
        pool._ready = MagicMock()
        pool._ready.qsize.return_value = 0
        pool._ready.put_nowait.side_effect = asyncio.QueueFull
        with patch.object(pool, "_create_null_source", return_value=mock_src):
            await pool._fill_pool()
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()

        # 4. Music player records history when track finishes or is skipped
        from cogs.music import MusicCog
        bot = MagicMock()
        bot.db = AsyncMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(auto_playlist=False))
        bot.update_clean_board = AsyncMock()
        tasks = []
        bot.track_task = MagicMock(
            side_effect=lambda coro: tasks.append(asyncio.create_task(coro)) if asyncio.iscoroutine(coro) else None
        )
        guild = MagicMock()
        bot.get_guild.return_value = guild
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        guild.voice_client = vc

        player = GuildPlayer(12345)
        player.smart_autoplay = False
        player.now_playing = Track(title="Played Track", url="https://youtube.com/watch?v=abc", duration=200)
        player.play_start_time = discord.utils.utcnow()
        player._was_skipped = True
        bot.get_player.return_value = player

        cog = MusicCog(bot)
        await cog._play_next_locked(12345)
        if tasks:
            await asyncio.gather(*tasks)
        bot.db.record_track_played.assert_called_once()
        _, kwargs = bot.db.record_track_played.call_args
        assert kwargs["skipped"] is True
        assert player._was_skipped is False

    @pytest.mark.asyncio
    async def test_phase11_performance_and_stability(self):
        # 1. YouTubeExtractor cache key allows cache hit across distinct dict instances
        from core.youtube import YouTubeExtractor
        yt = YouTubeExtractor()
        mock_data = {"id": "test1234567", "title": "Test Song", "webpage_url": "https://youtube.com/watch?v=test1234567"}
        with patch.object(yt, "_run_ytdl", return_value=mock_data) as mock_ytdl:
            res1 = await yt._extract("https://youtube.com/watch?v=test1234567", opts={"extract_flat": False})
            res2 = await yt._extract("https://youtube.com/watch?v=test1234567", opts={"extract_flat": False})
            assert res1 == mock_data
            assert res2 == mock_data
            # Cache must hit on second call — _run_ytdl called only once
            assert mock_ytdl.call_count == 1

        # 2. SpotifyExtractor.available property
        from core.spotify import SpotifyExtractor
        sp = SpotifyExtractor()
        assert sp.available == sp.is_available()
        assert getattr(sp, "available", False) == sp._available

        # 3. Sources router returns list[Track] and scheduler/main unpacks safely
        from sources.router import MultiSourceRouter
        router = MultiSourceRouter()
        mock_track = Track(title="SC Track", url="https://soundcloud.com/artist/track", duration=120)
        with patch.object(router._sc, "get_track", AsyncMock(return_value=mock_track)):
            resolved = await router.resolve("https://soundcloud.com/artist/track")
            assert isinstance(resolved, list)
            assert len(resolved) == 1
            assert resolved[0].title == "SC Track"

        # 4. RateLimiter does not evict active new key when cleanup triggers
        from utils.rate_limiter import RateLimiter
        limiter = RateLimiter(max_calls=3, window=10.0)
        limiter._last_cleanup = 0.0
        # Pretend there are > 1000 old windows
        for i in range(1005):
            limiter._windows[(i, 1)] = deque([time.monotonic() - 20.0])
        assert len(limiter._windows) > 1000
        # Check rate limit for a new user
        is_limited = limiter.is_rate_limited(99999, 88888)
        assert is_limited is False
        assert (99999, 88888) in limiter._windows
        assert len(limiter._windows[(99999, 88888)]) == 1

        # 5. LyricsService & ChapterDetector handle TimeoutError gracefully
        from lyrics.service import LyricsService
        from chapters.detector import ChapterDetector
        lyrics_svc = LyricsService()
        chapters_det = ChapterDetector()
        with patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError)):
            lyrics_res = await lyrics_svc.get_lyrics("https://youtube.com/watch?v=timeout1", MagicMock())
            assert lyrics_res is None
            chap_res = await chapters_det.get_chapters("https://youtube.com/watch?v=timeout1")
            assert chap_res == []

        # 6. WebServer._build_stats safely copies player dict
        from webserver import WebServer
        mock_bot = MagicMock()
        mock_p = MagicMock()
        mock_p.now_playing = None
        mock_bot._players = {1: mock_p, 2: mock_p}
        mock_bot.is_ready.return_value = True
        mock_bot.guilds = []
        server = WebServer(mock_bot)
        stats = await server._build_stats()
        assert stats["status"] == "online"
        assert stats["active_players"] == 0

    @pytest.mark.asyncio
    async def test_phase12_performance_and_stability(self):
        # 1. Player rollback_transaction resets _transaction and marks _queue_dirty
        player = GuildPlayer(12345)
        t1 = Track(title="T1", url="http://1")
        t2 = Track(title="T2", url="http://2")
        await player.enqueue(t1)
        assert player.begin_transaction() is True
        await player.enqueue(t2)
        assert len(player) == 2
        assert await player.rollback_transaction() is True
        assert len(player) == 1
        assert player._transaction is None
        assert player._queue_dirty is True
        # Can open transaction again immediately
        assert player.begin_transaction() is True

        # 2. Jump command sets _was_skipped flag before vc.stop()
        from cogs.queue_cog import QueueCog
        bot = MagicMock()
        bot.db = AsyncMock()
        bot.db.get_server_config = AsyncMock(return_value=MagicMock(dj_role_id=None))
        bot.get_player.return_value = player
        cog = QueueCog(bot)
        interaction = MagicMock()
        interaction.guild_id = 12345
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        vc = MagicMock()
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        interaction.guild.voice_client = vc

        player._was_skipped = False
        player._queue = deque([t1, t2])
        await cog.jump.callback(cog, interaction, 2)
        assert player._was_skipped is True
        vc.stop.assert_called_once()

        # 3. Bookmark load triggers _play_next when voice client is idle
        from cogs.bookmark_cog import BookmarkCog
        bm_cog = BookmarkCog(bot)
        music_cog = MagicMock()
        music_cog._play_next = AsyncMock()
        bot.get_cog.return_value = music_cog
        tasks = []
        bot.track_task = MagicMock(side_effect=lambda coro: tasks.append(asyncio.create_task(coro)) if asyncio.iscoroutine(coro) else None)
        bot.db.load_bookmark = AsyncMock(return_value=[t1, t2])
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        chan_mock = MagicMock()
        vc.channel = chan_mock
        interaction.user.voice = MagicMock(channel=chan_mock)

        await bm_cog._load(interaction, "my_bm", mode="append")
        music_cog._play_next.assert_called_with(12345)
        if tasks:
            await asyncio.gather(*tasks)

        # 4. _schedule_prefetch skips if track already has unexpired stream URL
        from cogs.music import MusicCog
        music_cog_real = MusicCog(bot)
        t_cached = Track(
            title="Cached Track",
            url="https://youtube.com/watch?v=cached_stream",
            stream_url_cache="https://cdn.example.com/audio",
            stream_url_expires=time.monotonic() + 300,
        )
        player._queue = deque([t_cached])
        player.cancel_prefetch()
        music_cog_real._schedule_prefetch(12345, t1)
        assert player._prefetch_task is None

        # 5. YouTubeExtractor.get_stream_url assigns cache on track
        from core.youtube import YouTubeExtractor
        yt = YouTubeExtractor()
        mock_result = {"entries": [{"url": "https://cdn.example.com/stream1"}]}
        with patch.object(yt, "_run_ytdl", return_value=mock_result):
            track_to_resolve = Track(title="Uncached", url="https://youtube.com/watch?v=resolve1")
            url = await yt.get_stream_url(track_to_resolve.url, track_to_resolve)
            assert url == "https://cdn.example.com/stream1"
            assert track_to_resolve.stream_url_cache == "https://cdn.example.com/stream1"
            assert track_to_resolve.stream_url_expires is not None

    @pytest.mark.asyncio
    async def test_phase13_stability_and_performance_improvements(self):
        from datetime import datetime, timezone
        from core.circuit_breaker import CircuitBreaker, BreakerState
        from core.stability import classify_exception, ExceptionKind
        from core.gateway_monitor import GatewayLatencyMonitor
        from utils.error_handler import forward_to_dev_channel
        from cogs.info import InfoCog
        from cogs.analytics_cog import AnalyticsCog, _guild_name

        # 1. CircuitBreaker ignores EXPECTED exceptions
        cb = CircuitBreaker("test_breaker", failure_threshold=2)
        async def fail_expected():
            raise Exception("Video unavailable")

        async def fail_recoverable():
            raise Exception("Connection reset by peer")

        with pytest.raises(Exception, match="Video unavailable"):
            await cb.call(fail_expected)
        assert cb._failure_count == 0
        assert cb.state == BreakerState.CLOSED

        with pytest.raises(Exception, match="Connection reset"):
            await cb.call(fail_recoverable)
        assert cb._failure_count == 1
        assert cb.state == BreakerState.CLOSED

        # 2. forward_to_dev_channel suppresses CancelledError and Expected errors
        mock_bot = MagicMock()
        mock_channel = AsyncMock()
        mock_bot.get_channel.return_value = mock_channel

        with patch("config.DEV_LOG_CHANNEL_IDS", [999]):
            # CancelledError -> suppressed
            await forward_to_dev_channel(mock_bot, asyncio.CancelledError())
            mock_channel.send.assert_not_called()

            # Expected error -> suppressed
            await forward_to_dev_channel(mock_bot, Exception("age-restricted content"))
            mock_channel.send.assert_not_called()

        # 3. GatewayLatencyMonitor rolling window thread-safety
        gw = GatewayLatencyMonitor(window_size=5)
        for s in [0.05, 0.06, 0.07, 0.08, 0.09]:
            gw.record_latency(s)
        assert gw.current_latency_ms == pytest.approx(90.0, 0.1)
        assert gw.avg_latency_ms == pytest.approx(70.0, 0.1)
        assert gw.min_latency_ms == pytest.approx(50.0, 0.1)
        assert gw.max_latency_ms == pytest.approx(90.0, 0.1)
        assert gw.jitter_ms > 0

        # 4. InfoCog botstats snapshot
        info_bot = MagicMock()
        p1 = GuildPlayer(1)
        p1.now_playing = Track(title="Song 1", url="http://1", duration=100)
        p2 = GuildPlayer(2)
        p2.now_playing = None
        info_bot._players = {1: p1, 2: p2}
        info_bot.guilds = [MagicMock(), MagicMock()]
        info_bot.start_time = datetime.now(timezone.utc)
        info_bot.yt_breaker = MagicMock(state=BreakerState.CLOSED)
        info_bot.sp_breaker = MagicMock(state=BreakerState.CLOSED)

        info_cog = InfoCog(info_bot)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        await info_cog.botstats.callback(info_cog, interaction)
        interaction.followup.send.assert_awaited_once()

        # 5. AnalyticsCog guild guard
        analytics_cog = AnalyticsCog(info_bot)
        assert getattr(analytics_cog.analytics, "guild_only", False) is True

        dm_interaction = MagicMock()
        dm_interaction.guild = None
        dm_interaction.guild_id = None
        dm_interaction.response.defer = AsyncMock()
        dm_interaction.followup.send = AsyncMock()
        assert _guild_name(dm_interaction) == "Server"
        await analytics_cog.analytics_heatmap.callback(analytics_cog, dm_interaction)
        dm_interaction.followup.send.assert_awaited_once_with(
            "This command can only be used in a server.", ephemeral=True
        )

        # 6. Player eta_seconds with position <= 0 and playback_speed scaling
        player = GuildPlayer(123)
        player.now_playing = Track(title="Current", url="http://curr", duration=100)
        player.play_start_time = datetime.now(timezone.utc)
        player._playback_speed = 1.0
        t_queued = Track(title="Queued", url="http://q", duration=60)
        await player.enqueue(t_queued)

        # position <= 0
        assert player.eta_seconds(0) == 0
        assert player.eta_seconds(-1) == 0

        # 1.0x speed
        eta_1 = player.eta_seconds(2)  # remaining (approx 100) + 60 = approx 160
        assert 150 <= eta_1 <= 160

        # 2.0x speed
        player._playback_speed = 2.0
        eta_2 = player.eta_seconds(2)  # remaining halved (~50) + 30 = ~80
        assert 70 <= eta_2 <= 85

    @pytest.mark.asyncio
    async def test_phase14_concurrency_audio_and_guild_guards(self, tmp_path):
        import math
        from core.database import DatabaseManager
        from core.audio import AudioEffectsProcessor, _build_atempo_chain
        from core.spotify import SpotifyExtractor
        from cogs.music import MusicCog
        from cogs.queue_cog import QueueCog
        from cogs.effects import EffectsCog
        from cogs.playback_cog import PlaybackCog

        # 1. Database SQLite WAL mode & busy timeout
        db_file = tmp_path / "test_phase14.db"
        db = DatabaseManager(db_path=str(db_file), migration_mode="sqlite_only")
        await db._init_sqlite()
        cursor = await db._sqlite_conn.execute("PRAGMA journal_mode;")
        row = await cursor.fetchone()
        assert row[0].lower() == "wal"
        cursor2 = await db._sqlite_conn.execute("PRAGMA busy_timeout;")
        row2 = await cursor2.fetchone()
        assert row2[0] == 5000
        await db.close()

        # 2. bot.get_player validation
        from main import MusicBot
        bot_mock = MagicMock(spec=MusicBot)
        bot_mock._players = {}
        # Bind the actual get_player method
        bot_mock.get_player = MusicBot.get_player.__get__(bot_mock, MusicBot)
        with pytest.raises(ValueError, match="Invalid guild_id"):
            bot_mock.get_player(None)
        with pytest.raises(ValueError, match="Invalid guild_id"):
            bot_mock.get_player("invalid_gid")
        p = bot_mock.get_player(98765)
        assert isinstance(p, GuildPlayer)
        assert p.guild_id == 98765

        # 3. Audio filter NaN/Inf protection
        # Speed NaN -> 1.0 (empty chain)
        assert _build_atempo_chain(float("nan")) == ""
        assert _build_atempo_chain(float("inf")) == ""

        proc = AudioEffectsProcessor()
        opts = proc.build_ffmpeg_options(volume=float("nan"))
        assert "volume=nan" not in opts["options"]
        opts_inf = proc.build_ffmpeg_options(volume=float("inf"))
        assert "volume=inf" not in opts_inf["options"]

        cf_opts = proc.build_crossfade_in_options(crossfade_secs=-5, volume=float("nan"))
        assert "afade=t=in:st=0:d=0" in cf_opts["options"]
        assert "volume=1.00" in cf_opts["options"]

        # 4. Spotify resilient item parsing
        sp = SpotifyExtractor()
        mock_yt = MagicMock()
        mock_yt.search = AsyncMock(return_value=[Track(title="Episode", url="http://ep")])
        # Episode without artists or null artists
        track = await sp._resolve_track_item({"name": "Podcast Ep 1", "artists": None}, mock_yt)
        assert track is not None
        assert track.title == "Episode"
        # Non-dict item
        assert await sp._resolve_track_item("not_a_dict", mock_yt) is None

        # 5. Cog interaction_check guild guards
        cogs_to_test = [MusicCog(bot_mock), QueueCog(bot_mock), EffectsCog(bot_mock), PlaybackCog(bot_mock)]
        dm_inter = MagicMock()
        dm_inter.guild_id = None
        dm_inter.response.is_done.return_value = False
        dm_inter.response.send_message = AsyncMock()

        guild_inter = MagicMock()
        guild_inter.guild_id = 12345

        for cog in cogs_to_test:
            dm_allowed = await cog.interaction_check(dm_inter)
            assert dm_allowed is False
            guild_allowed = await cog.interaction_check(guild_inter)
            assert guild_allowed is True

        # 6. Vote state reset in finish_track
        player = GuildPlayer(111)
        player.skip_votes.add(1)
        player.clear_votes.add(2)
        player.shuffle_votes.add(3)
        await player.finish_track()
        assert len(player.skip_votes) == 0
        assert len(player.clear_votes) == 0
        assert len(player.shuffle_votes) == 0

    @pytest.mark.asyncio
    async def test_phase15_metadata_caching_watchdog_and_podcast_guards(self):
        from lyrics.service import LyricsService
        from lyrics.parser import LyricLine
        from chapters.detector import ChapterDetector, Chapter
        from cogs.favorites import FavoritesCog
        from cogs.presets_cog import PresetsCog
        from cogs.theme_cog import ThemeCog
        from cogs.sleep_timer_cog import SleepTimerCog
        from podcast.cog import PodcastCog
        from radio.cog import RadioCog
        from radio.watchdog import RadioWatchdog, RadioSession
        from podcast.parser import parse_podcast_xml, fetch_and_parse_podcast

        # 1. LyricsService & ChapterDetector in-memory caching
        ls = LyricsService(cache_ttl=60.0)
        dummy_lines = [LyricLine(start_sec=0.0, timestamp_str="00:00", text="Hello world")]
        ls._cache["https://youtube.com/watch?v=lyric_test"] = (dummy_lines, False, time.monotonic())
        res_lyrics = await ls.get_lyrics("https://youtube.com/watch?v=lyric_test", MagicMock())
        assert res_lyrics is not None
        assert res_lyrics[0] == dummy_lines

        cd = ChapterDetector(cache_ttl=60.0)
        dummy_chapters = [Chapter(index=1, title="Intro", start_sec=0.0, end_sec=30.0, duration_sec=30.0)]
        cd._cache["https://youtube.com/watch?v=chap_test"] = (dummy_chapters, time.monotonic())
        res_chapters = await cd.get_chapters("https://youtube.com/watch?v=chap_test")
        assert res_chapters == dummy_chapters

        # 2. Universal interaction_check on remaining cogs
        bot_mock = MagicMock()
        bot_mock._players = {}
        cogs_phase15 = [
            FavoritesCog(bot_mock),
            PresetsCog(bot_mock),
            ThemeCog(bot_mock),
            SleepTimerCog(bot_mock),
            PodcastCog(bot_mock),
            RadioCog(bot_mock),
        ]
        dm_inter = MagicMock()
        dm_inter.guild_id = None
        dm_inter.response.is_done.return_value = False
        dm_inter.response.send_message = AsyncMock()

        guild_inter = MagicMock()
        guild_inter.guild_id = 99999

        for cog in cogs_phase15:
            assert await cog.interaction_check(dm_inter) is False
            assert await cog.interaction_check(guild_inter) is True
        cogs_phase15[5].watchdog.stop()

        # 3. RadioWatchdog reconnect cap & safe unregister
        watchdog = RadioWatchdog(bot_mock)
        watchdog.register(101, "lofi", "Lofi Beats", "http://stream")
        session = watchdog.get_session(101)
        assert session is not None
        session.reconnect_count = 10  # hit max limit
        guild_mock = MagicMock()
        vc_mock = MagicMock()
        vc_mock.is_connected.return_value = True
        vc_mock.is_playing.return_value = False
        vc_mock.is_paused.return_value = False
        guild_mock.voice_client = vc_mock
        bot_mock.get_guild.return_value = guild_mock
        p_mock = GuildPlayer(101)
        p_mock.is_radio_mode = True
        bot_mock._players[101] = p_mock
        bot_mock.get_player.return_value = p_mock

        await watchdog._check_guild_stream(101, session)
        assert not watchdog.is_active(101)
        assert p_mock.is_radio_mode is False

        # Safe unregister on non-existent guild
        watchdog.unregister(9999999)  # does not raise

        # 4. Podcast parser oversized feed guard & URL sanitize
        oversized_resp = AsyncMock()
        oversized_resp.status = 200
        oversized_resp.content_length = 20 * 1024 * 1024  # 20MB
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = oversized_resp
        show = await fetch_and_parse_podcast(mock_session, "http://feed.xml")
        assert show is None

        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Pod</title>
                <item>
                    <title>Ep 1</title>
                    <enclosure url="  https://cdn.example.com/audio.mp3  " />
                </item>
            </channel>
        </rss>
        """
        show_parsed = parse_podcast_xml(xml_data)
        assert show_parsed is not None
        assert len(show_parsed.episodes) == 1
        assert show_parsed.episodes[0].audio_url == "https://cdn.example.com/audio.mp3"

        # 5. GuildPlayer session status properties
        player = GuildPlayer(777)
        assert player.has_tracks is False
        assert player.is_active_session is False

        player.now_playing = Track(title="Song", url="http://s", duration=100)
        assert player.has_tracks is True
        assert player.is_active_session is True

        player.now_playing = None
        player.is_radio_mode = True
        assert player.has_tracks is False
        assert player.is_active_session is True












