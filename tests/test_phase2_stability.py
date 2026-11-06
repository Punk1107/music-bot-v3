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
        player.clear_queue.assert_called_once()

        # Shuffle button
        await cast(Any, view.btn_shuffle.callback)(interaction)
        player.shuffle.assert_called_once()

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
