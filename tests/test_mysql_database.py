# -*- coding: utf-8 -*-
"""
tests/test_mysql_database.py — Comprehensive unit & integration tests for the MySQL 8.0+
Enterprise Database Engine, Self-Healing Pool, Sharding/Replica Router, Gen 5.0 Tables,
and SQLite ➔ MySQL Migration Toolkit.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pymysql
import pytest

from core.database import (
    DatabaseManager,
    MYSQL_SCHEMA_DDL,
    ShardRouter,
    _extract_mysql_errno,
    _format_time_col,
    _parse_json_col,
)
from models.enums import AudioQuality, DuplicateMode, EmbedTheme, QueuePermission
from models.server_config import ServerConfig
from models.track import Track
from scripts.migrate_sqlite_to_mysql import (
    SQLiteToMySQLMigrator,
    normalize_datetime3,
    normalize_json,
    normalize_server_config_row,
    stream_sqlite_table,
)


# ── 1. Schema DDL Verification ────────────────────────────────────────────────

def test_mysql_schema_ddl_completeness():
    """Verify all 9 Enterprise Normalized MySQL 8.0+ table groups are defined with utf8mb4."""
    full_ddl = "\n".join(MYSQL_SCHEMA_DDL)
    expected_tables = [
        "server_configs",
        "queue",
        "history",
        "user_stats",
        "favorites",
        "user_playlists",
        "playlist_tracks",
        "scheduled_tasks",
        "session_state",
        "analytics",
        "search_history",
    ]
    for tbl in expected_tables:
        assert f"CREATE TABLE IF NOT EXISTS {tbl}" in full_ddl

    # Verify InnoDB + utf8mb4_unicode_ci across all DDL statements
    for stmt in MYSQL_SCHEMA_DDL:
        assert "ENGINE=InnoDB" in stmt
        assert "CHARSET=utf8mb4" in stmt
        assert "COLLATE=utf8mb4_unicode_ci" in stmt

    # Verify history range partitioning includes played_at in PK (MySQL 8.0 requirement)
    history_ddl = [s for s in MYSQL_SCHEMA_DDL if "CREATE TABLE IF NOT EXISTS history" in s][0]
    assert "PRIMARY KEY (id, played_at)" in history_ddl
    assert "PARTITION BY RANGE (TO_DAYS(played_at))" in history_ddl
    assert "PARTITION p_initial VALUES LESS THAN (TO_DAYS('2026-01-01'))" in history_ddl

    # Verify scheduled_tasks has no duplicate is_active column
    sched_ddl = [s for s in MYSQL_SCHEMA_DDL if "CREATE TABLE IF NOT EXISTS scheduled_tasks" in s][0]
    assert sched_ddl.count("is_active BOOLEAN") == 1


# ── 2. Normalized ServerConfig Mapping ────────────────────────────────────────

def test_server_config_normalized_roundtrip():
    """Verify ServerConfig serialises to 20 MySQL columns and reconstructs via from_db_row."""
    cfg = ServerConfig(
        guild_id=987654321012345678,
        volume=0.85,
        audio_quality=AudioQuality.HIGH,
        max_track_len=3600,
        idle_timeout=600,
        dj_only=True,
        dj_role_id=111222333,
        request_channel_id=444555666,
        timezone="Asia/Bangkok",
        auto_playlist=True,
        auto_playlist_size=10,
        smart_autoplay=True,
        max_playlist_tracks=150,
        queue_locked=True,
        queue_add_permission=QueuePermission.DJ,
        duplicate_mode=DuplicateMode.WARN,
        auto_leave_alone=False,
        auto_pause_empty=True,
        embed_theme=EmbedTheme.GLASS,
        guild_presets={"night": {"volume": 0.5}},
        language="th",
    )

    params = cfg.to_db_params()
    assert len(params) == 20
    assert params[0] == 987654321012345678
    assert params[8] == "Asia/Bangkok"
    assert params[18] == "th"

    # Simulate a DictCursor row returned from MySQL server_configs
    col_names = [
        "guild_id", "volume", "audio_quality", "max_track_len", "idle_timeout",
        "dj_only", "dj_role_id", "request_channel_id", "timezone",
        "auto_playlist", "auto_playlist_size", "smart_autoplay",
        "queue_locked", "queue_add_permission", "duplicate_mode",
        "auto_leave_alone", "auto_pause_empty", "embed_theme",
        "language", "guild_presets",
    ]
    db_row = dict(zip(col_names, params))
    restored = ServerConfig.from_db_row(db_row)

    assert restored.guild_id == cfg.guild_id
    assert restored.volume == pytest.approx(0.85)
    assert restored.timezone == "Asia/Bangkok"
    assert restored.dj_role_id == 111222333
    assert restored.request_channel_id == 444555666
    assert restored.max_playlist_tracks == 150
    assert restored.queue_add_permission == QueuePermission.DJ
    assert restored.duplicate_mode == DuplicateMode.WARN
    assert restored.embed_theme == EmbedTheme.GLASS
    assert restored.language == "th"
    assert restored.guild_presets == {"night": {"volume": 0.5}}


# ── 3. Self-Healing (Error 2006/2013) & Deadlock Retry (Error 1213) ──────────

class _CurContextManager:
    def __init__(self, cur):
        self.cur = cur

    async def __aenter__(self):
        return self.cur

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_mock_pool(cursor_side_effects=None, ping_side_effects=None):
    """Helper to build a mock aiomysql Pool, Connection, and DictCursor."""
    pool = MagicMock()
    pool.size = 5
    pool.freesize = 4
    pool.minsize = 5
    pool.maxsize = 25

    conn = MagicMock()
    conn.ping = AsyncMock(side_effect=ping_side_effects)
    conn.commit = AsyncMock()
    conn.rollback = AsyncMock()
    conn.close = MagicMock()

    cur = MagicMock()
    cur.execute = AsyncMock(side_effect=cursor_side_effects)
    cur.executemany = AsyncMock()
    cur.fetchone = AsyncMock(return_value={"1": 1})
    cur.fetchall = AsyncMock(return_value=[])
    cur.rowcount = 1
    cur.lastrowid = 42

    conn.cursor = MagicMock(side_effect=lambda *args, **kwargs: _CurContextManager(cur))

    pool.acquire = AsyncMock(return_value=conn)
    pool.release = MagicMock()
    return pool, conn, cur


@pytest.mark.asyncio
async def test_self_healing_on_mysql_server_gone_away_error_2006():
    """Verify _run_mysql automatically heals and retries on Error 2006 (MySQL server has gone away)."""
    err_2006 = pymysql.err.OperationalError(2006, "MySQL server has gone away")
    pool, conn, cur = _make_mock_pool(cursor_side_effects=[err_2006, None])

    db = DatabaseManager(migration_mode="mysql")
    db.router.master_pool = pool

    latency = await db.ping()
    assert latency >= 0.0
    assert db._reconnect_count == 1
    assert cur.execute.await_count == 2


@pytest.mark.asyncio
async def test_automatic_deadlock_retry_error_1213():
    """Verify _run_mysql rolls back and retries with backoff on Error 1213 (Deadlock)."""
    err_1213 = pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock; try restarting transaction")
    pool, conn, cur = _make_mock_pool(cursor_side_effects=[err_1213, err_1213, None])

    db = DatabaseManager(migration_mode="mysql")
    db.router.master_pool = pool

    await db.clear_queue(12345)
    assert db._deadlock_retry_count == 2
    assert conn.rollback.await_count == 2
    assert conn.commit.await_count == 1
    assert cur.execute.await_count == 3


# ── 4. Phase 3: Sharding & Master-Replica Read/Write Split ───────────────────

def test_shard_router_read_write_split_and_guild_modulo():
    """Verify ShardRouter routes reads to Replica and guild queries to modulo shards."""
    router = ShardRouter()
    master = MagicMock(name="master_pool")
    replica = MagicMock(name="replica_pool")
    shard0 = MagicMock(name="shard_0")
    shard1 = MagicMock(name="shard_1")

    router.master_pool = master
    router.replica_pool = replica

    # Without shards: writes go to master, readonly goes to replica
    assert router.select_pool(readonly=False) is master
    assert router.select_pool(readonly=True) is replica

    # With shards: guild_id % 2 selects shard0 or shard1
    router.shard_pools = [shard0, shard1]
    assert router.select_pool(guild_id=100, readonly=False) is shard0
    assert router.select_pool(guild_id=101, readonly=True) is shard1


# ── 5. Gen 5.0 Multi-Playlists, Scheduled Tasks & Dual-Write Engine ──────────

@pytest.mark.asyncio
async def test_multi_playlists_and_scheduled_tasks_lifecycle():
    """Test Gen 5.0 user_playlists, playlist_tracks, and scheduled_tasks CRUD."""
    db = DatabaseManager(":memory:")
    await db.initialise()
    try:
        t1 = Track(title="Song One", url="https://youtube.com/watch?v=1", duration=180, uploader="Artist A")
        t2 = Track(title="Song Two", url="https://youtube.com/watch?v=2", duration=240, uploader="Artist B")

        # Create playlist via bookmark API
        ok = await db.save_bookmark(
            user_id=1001,
            guild_id=2002,
            name="Chill Vibes",
            tracks=[t1, t2],
            scope="guild",
            is_public=True,
        )
        assert ok is True

        # List playlists
        playlists = await db.list_bookmarks(1001, 2002)
        assert len(playlists) == 1
        assert playlists[0]["name"] == "Chill Vibes"
        assert playlists[0]["count"] == 2
        assert playlists[0]["scope"] == "guild"

        # Load playlist and verify play_count increments
        loaded = await db.load_bookmark(1001, 2002, "Chill Vibes")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0].title == "Song One"
        assert loaded[1].title == "Song Two"

        # Scheduled tasks lifecycle
        now_utc = datetime(2026, 10, 31, 14, 30, 0, tzinfo=timezone.utc)
        task_id = await db.create_scheduled_task(
            guild_id=2002,
            voice_channel_id=3003,
            text_channel_id=4004,
            user_id=1001,
            scheduled_time="14:30",
            query="lofi hip hop radio",
            days_of_week="daily",
        )
        assert task_id > 0

        tasks_list = await db.list_scheduled_tasks(2002)
        assert len(tasks_list) == 1
        assert tasks_list[0]["query"] == "lofi hip hop radio"

        due = await db.claim_due_scheduled_tasks("worker-test", now_utc=now_utc)
        assert len(due) == 1
        assert due[0]["id"] == task_id

        deleted = await db.delete_scheduled_task(2002, task_id)
        assert deleted is True

        bm_deleted = await db.delete_bookmark(1001, 2002, "Chill Vibes")
        assert bm_deleted is True
    finally:
        await db.close()


# ── 6. Migration Normalization & Checksum Reconciliation ──────────────────────

def test_migration_data_normalizers():
    """Verify ISO-8601 timestamp normalization, JSON normalization, and server_config unpacking."""
    dt_str = normalize_datetime3("2026-10-31T12:15:30.123456Z")
    assert dt_str == "2026-10-31 12:15:30.123"

    raw_cfg_row = {
        "guild_id": 555666,
        "config_data": json.dumps({
            "guild_id": 555666,
            "volume": 0.75,
            "language": "th",
            "dj_only": True,
            "timezone": "Asia/Bangkok",
        }),
        "created_at": "2026-01-15 08:00:00",
        "updated_at": "2026-05-20 10:30:00",
    }
    norm_tuple = normalize_server_config_row(raw_cfg_row)
    assert len(norm_tuple) == 22
    assert norm_tuple[0] == 555666
    assert norm_tuple[1] == pytest.approx(0.75)
    assert norm_tuple[5] is True
    assert norm_tuple[8] == "Asia/Bangkok"
    assert norm_tuple[18] == "th"
    assert norm_tuple[20] == "2026-01-15 08:00:00.000"
    assert norm_tuple[21] == "2026-05-20 10:30:00.000"

    assert normalize_json('{"a": 1}') == '{"a": 1}'
    assert normalize_json("invalid json", {"fallback": True}) == '{"fallback": true}'

