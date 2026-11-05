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
from unittest.mock import AsyncMock, MagicMock, patch

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
        assert len(base_en) == 304, f"Expected 304 keys, got {len(base_en)}"

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
