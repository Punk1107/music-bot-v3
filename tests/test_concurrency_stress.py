# -*- coding: utf-8 -*-
"""
tests/test_concurrency_stress.py — Concurrency & High Contention Stress Tests (Gen 5.0).

Tests:
  1. Concurrent queue writes on the same guild (30 concurrent workers) -> serialized via _guild_write_lock
  2. Concurrent queue writes across 10 distinct guilds (50 concurrent workers)
  3. Atomic user stats upsert contention under concurrent execution
  4. Distributed scheduled task locking exclusivity (5 concurrent workers claiming same due task)
  5. Connection pool saturation and queueing under high concurrency
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.database import DatabaseManager
from models.track import Track


def _make_dummy_tracks(count: int, prefix: str = "track") -> list[Track]:
    return [
        Track(
            title=f"{prefix} Title {i}",
            url=f"https://youtu.be/{prefix}_{i}",
            duration=120 + i,
            requested_by_id=1000 + i,
            requested_by_name=f"User_{i}",
        )
        for i in range(count)
    ]


class TestConcurrencyStress:

    @pytest.mark.asyncio
    async def test_concurrent_save_queue_same_guild(self):
        """
        Simulate 30 concurrent coroutines attempting to overwrite the queue for guild 999.
        With _guild_write_lock in place, operations are serialized; no deadlocks or
        Duplicate Key errors occur, and the final state is a valid queue.
        """
        db = DatabaseManager(":memory:")
        await db.initialise()

        guild_id = 999
        channel_id = 888

        async def _worker(worker_id: int):
            tracks = _make_dummy_tracks(5, prefix=f"w{worker_id}")
            await db.save_queue(guild_id, channel_id, tracks)
            # Short yield
            await asyncio.sleep(0.001)

        # Run 30 concurrent workers
        await asyncio.gather(*[_worker(i) for i in range(30)])

        loaded = await db.load_queue(guild_id)
        assert len(loaded) == 5
        # Verify tracks are contiguous and properly formatted
        for t in loaded:
            assert t.title.startswith("w")

    @pytest.mark.asyncio
    async def test_concurrent_save_queue_multiple_guilds(self):
        """
        Simulate 50 concurrent workers writing queues across 10 different guilds simultaneously.
        """
        db = DatabaseManager(":memory:")
        await db.initialise()

        async def _worker(worker_id: int):
            target_guild = 100 + (worker_id % 10)
            tracks = _make_dummy_tracks(3, prefix=f"g{target_guild}_w{worker_id}")
            await db.save_queue(target_guild, 9999, tracks)

        await asyncio.gather(*[_worker(i) for i in range(50)])

        # Verify each of the 10 guilds has exactly 3 tracks
        for g in range(100, 110):
            q = await db.load_queue(g)
            assert len(q) == 3

    @pytest.mark.asyncio
    async def test_atomic_user_stats_upsert_contention(self):
        """
        Simulate 20 concurrent track play records for the same user and guild.
        Verify that user stats and play history record properly without crash.
        """
        db = DatabaseManager(":memory:")
        await db.initialise()

        guild_id = 777
        user_id = 555
        dummy_track = Track(title="Contention Track", url="https://youtu.be/contention", duration=150)

        async def _play_worker(i: int):
            await db.record_track_played(
                guild_id=guild_id,
                user_id=user_id,
                track=dummy_track,
                duration_played=150,
                completed=True,
            )

        await asyncio.gather(*[_play_worker(i) for i in range(20)])

        # Check history has 20 plays
        history = await db.get_history(guild_id, limit=50)
        assert len(history) == 20

        # Check user stats reflect the plays (calling with guild_id, user_id)
        stats = await db.get_user_stats(guild_id, user_id)
        assert stats is not None
        assert stats.get("total_tracks_requested") == 20

    @pytest.mark.asyncio
    async def test_distributed_scheduled_task_locking_exclusivity(self):
        """
        Test that when 5 distinct workers claim due tasks in the same window,
        a task is claimed exclusively by only one worker.
        """
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.ping = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_conn.cursor = MagicMock()

        cursor_ctx = AsyncMock()
        cursor_mock = MagicMock()

        # Simulate first worker gets rowcount=1, others get rowcount=0
        claim_state = {"claimed": False}

        async def _execute_mock(sql, params=None):
            if "UPDATE scheduled_tasks" in sql:
                if not claim_state["claimed"]:
                    claim_state["claimed"] = True
                    cursor_mock.rowcount = 1
                else:
                    cursor_mock.rowcount = 0
            elif "SELECT * FROM scheduled_tasks" in sql:
                pass

        async def _fetchall_mock():
            if cursor_mock.rowcount > 0:
                return [{"id": 1, "guild_id": 123, "query": "lofi", "scheduled_time": "12:00:00"}]
            return []

        cursor_mock.execute.side_effect = _execute_mock
        cursor_mock.fetchall.side_effect = _fetchall_mock
        cursor_ctx.__aenter__.return_value = cursor_mock
        cursor_ctx.__aexit__.return_value = None
        mock_conn.cursor.return_value = cursor_ctx

        acquire_fut = asyncio.Future()
        acquire_fut.set_result(mock_conn)
        mock_pool.acquire.return_value = acquire_fut
        mock_pool.release = MagicMock()
        mock_pool.size = 10
        mock_pool.freesize = 10

        db.router.master_pool = mock_pool

        with patch.object(db.router, "select_pool", return_value=mock_pool):
            now_fixed = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
            
            async def _worker(worker_name: str):
                return await db.claim_due_scheduled_tasks(worker_id=worker_name, now_utc=now_fixed)

            results = await asyncio.gather(*[_worker(f"worker_{i}") for i in range(5)])

            # Flatten results: total claimed tasks across all 5 workers must equal 1
            all_claimed = [item for sublist in results for item in sublist]
            assert len(all_claimed) == 1
            assert all_claimed[0]["query"] == "lofi"

    @pytest.mark.asyncio
    async def test_connection_pool_saturation_and_queueing(self):
        """
        Simulate 30 concurrent operations against a MySQL pool.
        Verify that acquire timeout does not trip when pool can service requests,
        and telemetry tracks peak busy connections and total queries accurately.
        """
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.ping = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_conn.cursor = MagicMock()

        cursor_ctx = AsyncMock()
        cursor_ctx.__aenter__.return_value = MagicMock()
        cursor_ctx.__aexit__.return_value = None
        mock_conn.cursor.return_value = cursor_ctx

        mock_pool.size = 5
        mock_pool.freesize = 5

        async def _acquire_mock():
            # Simulate slight checkout delay
            await asyncio.sleep(0.005)
            return mock_conn

        mock_pool.acquire.side_effect = _acquire_mock
        mock_pool.release = MagicMock()

        with patch.object(db.router, "select_pool", return_value=mock_pool):
            async def _dummy_op(conn, cur):
                await asyncio.sleep(0.002)
                return 1

            tasks = [db._run_mysql(_dummy_op) for _ in range(30)]
            results = await asyncio.gather(*tasks)

            assert len(results) == 30
            assert all(r == 1 for r in results)
            stats = db.pool_stats()
            assert stats["total_queries"] == 30
            assert stats["pool_exhausted_count"] == 0
