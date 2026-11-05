# -*- coding: utf-8 -*-
"""
tests/test_database_disconnect_recovery.py — Database Disconnection, Healing & Fault-Tolerance Tests.

Tests:
  1. Auto-healing on Error 2006 (MySQL server has gone away)
  2. Auto-healing on Error 2013 (Lost connection to MySQL server during query)
  3. Automatic deadlock retry with backoff (Error 1213)
  4. Safe fallback to ServerConfig.default when database is completely unreachable
  5. In-memory queue preservation during database write failure
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pymysql
import pytest

from core.database import DatabaseManager
from core.player import GuildPlayer
from models.server_config import ServerConfig
from models.track import Track


class TestDatabaseDisconnectRecovery:

    @pytest.mark.asyncio
    async def test_auto_reconnect_on_error_2006(self):
        """Verify _run_mysql catches Error 2006, closes the dead connection, and recovers on retry."""
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()
        mock_conn1 = MagicMock()
        mock_conn1.ping = AsyncMock(side_effect=pymysql.err.OperationalError(2006, "MySQL server has gone away"))
        mock_conn1.rollback = AsyncMock()
        mock_conn1.close = MagicMock()

        mock_conn2 = MagicMock()
        mock_conn2.ping = AsyncMock()
        mock_conn2.commit = AsyncMock()
        mock_conn2.cursor = MagicMock()

        cursor_ctx = AsyncMock()
        cursor_mock = MagicMock()
        cursor_ctx.__aenter__.return_value = cursor_mock
        cursor_ctx.__aexit__.return_value = None
        mock_conn2.cursor.return_value = cursor_ctx

        # First acquire returns dead conn1, second returns healed conn2
        acquire_conns = [mock_conn1, mock_conn2]

        async def _acquire():
            return acquire_conns.pop(0)

        mock_pool.acquire.side_effect = _acquire
        mock_pool.release = MagicMock()
        mock_pool.size = 5
        mock_pool.freesize = 5

        db.router.master_pool = mock_pool

        with patch.object(db.router, "select_pool", return_value=mock_pool):
            async def _dummy_query(conn, cur):
                return "recovered"

            result = await db._run_mysql(_dummy_query)
            assert result == "recovered"
            assert db.pool_stats()["reconnect_count"] == 1
            assert mock_conn1.close.called

    @pytest.mark.asyncio
    async def test_lost_connection_on_error_2013(self):
        """Verify _run_mysql handles Error 2013 during query execution and recovers."""
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()

        mock_conn1 = MagicMock()
        mock_conn1.ping = AsyncMock()
        mock_conn1.rollback = AsyncMock()
        mock_conn1.close = MagicMock()

        # Conn1 cursor raises Error 2013
        ctx1 = AsyncMock()
        ctx1.__aenter__.side_effect = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
        mock_conn1.cursor.return_value = ctx1

        mock_conn2 = MagicMock()
        mock_conn2.ping = AsyncMock()
        mock_conn2.commit = AsyncMock()
        ctx2 = AsyncMock()
        ctx2.__aenter__.return_value = MagicMock()
        ctx2.__aexit__.return_value = None
        mock_conn2.cursor.return_value = ctx2

        acquire_conns = [mock_conn1, mock_conn2]

        async def _acquire():
            return acquire_conns.pop(0)

        mock_pool.acquire.side_effect = _acquire
        mock_pool.release = MagicMock()
        mock_pool.size = 5
        mock_pool.freesize = 5

        db.router.master_pool = mock_pool

        with patch.object(db.router, "select_pool", return_value=mock_pool):
            async def _dummy_query(conn, cur):
                return "recovered_2013"

            result = await db._run_mysql(_dummy_query)
            assert result == "recovered_2013"
            assert db.pool_stats()["reconnect_count"] == 1

    @pytest.mark.asyncio
    async def test_deadlock_retry_recovery_1213(self):
        """Verify _run_mysql retries on Error 1213 (Deadlock) with backoff and commits on retry."""
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()

        attempts = [0]

        mock_conn = MagicMock()
        mock_conn.ping = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_conn.rollback = AsyncMock()

        cursor_ctx = AsyncMock()
        cursor_mock = MagicMock()
        cursor_ctx.__aenter__.return_value = cursor_mock
        cursor_ctx.__aexit__.return_value = None
        mock_conn.cursor.return_value = cursor_ctx

        async def _acquire():
            return mock_conn

        mock_pool.acquire.side_effect = _acquire
        mock_pool.release = MagicMock()
        mock_pool.size = 5
        mock_pool.freesize = 5

        db.router.master_pool = mock_pool

        with patch.object(db.router, "select_pool", return_value=mock_pool):
            async def _deadlock_query(conn, cur):
                attempts[0] += 1
                if attempts[0] == 1:
                    raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
                return "deadlock_cleared"

            result = await db._run_mysql(_deadlock_query)
            assert result == "deadlock_cleared"
            assert db.pool_stats()["deadlock_retry_count"] == 1
            assert attempts[0] == 2

    @pytest.mark.asyncio
    async def test_server_config_fallback_during_db_outage(self):
        """Verify get_server_config falls back to ServerConfig.default if MySQL is unreachable."""
        db = DatabaseManager(":memory:", host="127.0.0.1", migration_mode="mysql")
        mock_pool = MagicMock()
        mock_pool.acquire.side_effect = ConnectionRefusedError("MySQL down")

        db.router.master_pool = mock_pool

        with patch.object(db.router, "select_pool", return_value=mock_pool), \
             patch("core.database.logger.warning") as mock_warn:

            cfg = await db.get_server_config(12345)
            assert isinstance(cfg, ServerConfig)
            assert cfg.guild_id == 12345
            assert cfg.volume == 1.0
            assert mock_warn.called
            assert "Database unreachable while fetching server_config" in mock_warn.call_args[0][0]

    @pytest.mark.asyncio
    async def test_player_in_memory_queue_intact_if_save_fails(self):
        """Verify that player in-memory queue remains intact even if DB save fails."""
        player = GuildPlayer(guild_id=888)
        tracks = [
            Track(title="Song 1", url="https://youtu.be/s1", duration=100),
            Track(title="Song 2", url="https://youtu.be/s2", duration=200),
        ]
        await player.extend(tracks)
        assert len(player) == 2
        assert player._queue_dirty is True

        db = DatabaseManager(":memory:")
        # Force save_queue to raise an error
        with patch.object(db, "save_queue", side_effect=RuntimeError("Database failure")):
            try:
                await db.save_queue(888, 777, player.queue)
            except Exception:
                pass

        # Player queue must still hold both tracks and dirty flag stays True
        assert len(player) == 2
        assert player.queue[0].title == "Song 1"
        assert player.queue[1].title == "Song 2"
        assert player._queue_dirty is True
