# -*- coding: utf-8 -*-
"""
core/database.py — Enterprise Async MySQL 8.0+ Database Manager for Music Bot V3 (Gen 5.0–6.0).

Architecture Overview:
  1. Async Connection Pool (aiomysql / PyMySQL):
     - Host/Port/User/Pass/DB from .env (default 127.0.0.1:3306)
     - Pool Min Size: 5, Pool Max Size: 25
     - Charset: utf8mb4 (Emoji, Thai, Japanese, Chinese support)
     - Autocommit: False (explicit ACID transactions)
  2. Self-Healing & Resilience Engine:
     - Auto-Ping (SELECT 1 / conn.ping(reconnect=True))
     - Handles MySQL Server has gone away (Error 2006) & Lost connection (Error 2013)
     - Automatic Deadlock Retry (Error 1213 & Lock Wait Timeout Error 1205) with exponential backoff
  3. Enterprise Normalized Schema (9 Core Table Groups):
     - server_configs (Normalized 20 columns + JSON guild_presets)
     - queue (Active queue with uk_guild_position)
     - history (Range Partitioned by TO_DAYS(played_at) with auto-partition maintenance)
     - user_stats (Atomic upsert per user/guild)
     - favorites (Personal favorites with uk_user_guild_name)
     - user_playlists & playlist_tracks (Gen 5.0 Multi-Playlist with FK ON DELETE CASCADE)
     - scheduled_tasks (Gen 5.0 Scheduled Playback with distributed worker locking)
     - session_state (Gen 5.0 Crash/Shutdown recovery with heartbeat)
     - analytics (In-memory batch buffer + 90-day automated retention policy)
     - search_history (Autocomplete query cache)
  4. Zero-Downtime Migration & Evolutionary Roadmap (Phase 1–3):
     - Offline & Zero-Downtime Dual-Write mode (mysql | dual_write | sqlite_fallback)
     - Checksum & Row Count Reconciliation (verify_migration_checksums)
     - Phase 2: Independent Worker/Dashboard Pool & Distributed Task Locking (locked_by)
     - Phase 3: Master-Replica Read/Write Split & Guild Modulo Sharding (ShardRouter)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, TypeVar

import aiosqlite
import aiomysql
import pymysql
from pymysql.constants import ER

import config
from models.server_config import ServerConfig
from models.track import Track

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Analytics write buffer ────────────────────────────────────────────────────
ANALYTICS_FLUSH_INTERVAL: float = 5.0    # seconds between auto-flushes
ANALYTICS_FLUSH_SIZE:     int   = 50     # flush early if buffer this full
ANALYTICS_BUF_MAX:        int   = 2000   # hard cap on buffered events to prevent OOM

# ── Server config cache ───────────────────────────────────────────────────────
_CFG_CACHE_TTL: float = 30.0             # cache guild config for 30 s

# ── MySQL Error Codes for Self-Healing & Deadlock Retry ───────────────────────
MYSQL_CONN_LOST_CODES: frozenset[int] = frozenset({
    2006,  # CR_SERVER_GONE_ERROR: MySQL server has gone away
    2013,  # CR_SERVER_LOST: Lost connection to MySQL server during query
    2055,  # CR_SERVER_LOST_EXTENDED
    4031,  # ER_CLIENT_INTERACTION_TIMEOUT
})
MYSQL_DEADLOCK_CODES: frozenset[int] = frozenset({
    1213,  # ER_LOCK_DEADLOCK: Deadlock found when trying to get lock
    1205,  # ER_LOCK_WAIT_TIMEOUT: Lock wait timeout exceeded
})

# ── Enterprise Normalized MySQL 8.0+ Schema DDL ───────────────────────────────

MYSQL_SCHEMA_DDL: list[str] = [
    # ── 1. Server Configs ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS server_configs (
        guild_id BIGINT UNSIGNED PRIMARY KEY,
        volume FLOAT NOT NULL DEFAULT 1.0,
        audio_quality VARCHAR(16) NOT NULL DEFAULT 'high',
        max_track_len INT UNSIGNED NOT NULL DEFAULT 7200,
        idle_timeout INT UNSIGNED NOT NULL DEFAULT 300,
        dj_only BOOLEAN NOT NULL DEFAULT FALSE,
        dj_role_id BIGINT UNSIGNED NULL,
        request_channel_id BIGINT UNSIGNED NULL,
        timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
        auto_playlist BOOLEAN NOT NULL DEFAULT FALSE,
        auto_playlist_size INT UNSIGNED NOT NULL DEFAULT 5,
        smart_autoplay BOOLEAN NOT NULL DEFAULT FALSE,
        queue_locked BOOLEAN NOT NULL DEFAULT FALSE,
        queue_add_permission VARCHAR(16) NOT NULL DEFAULT 'everyone',
        duplicate_mode VARCHAR(16) NOT NULL DEFAULT 'allow',
        auto_leave_alone BOOLEAN NOT NULL DEFAULT TRUE,
        auto_pause_empty BOOLEAN NOT NULL DEFAULT TRUE,
        embed_theme VARCHAR(32) NOT NULL DEFAULT 'classic',
        language VARCHAR(8) NOT NULL DEFAULT 'en',
        guild_presets JSON NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 2. Active Queue ───────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS queue (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        channel_id BIGINT UNSIGNED NOT NULL,
        track_data JSON NOT NULL,
        position INT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
        added_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_queue_guild_pos (guild_id, position),
        UNIQUE KEY uk_guild_position (guild_id, position),
        INDEX idx_queue_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 3. Play History (Range Partitioned by TO_DAYS(played_at)) ─────────────
    # Note: MySQL 8.0 InnoDB requires the partitioning key (played_at) in PRIMARY KEY.
    # Includes p_initial (< 2026-01-01) plus current/future year partitions and p_max.
    """
    CREATE TABLE IF NOT EXISTS history (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        track_data JSON NOT NULL,
        played_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        duration_played INT UNSIGNED DEFAULT 0,
        skipped BOOLEAN DEFAULT FALSE,
        completed BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (id, played_at),
        INDEX idx_history_guild_played (guild_id, played_at DESC),
        INDEX idx_history_user (user_id),
        INDEX idx_history_played_at (played_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    PARTITION BY RANGE (TO_DAYS(played_at)) (
        PARTITION p_initial VALUES LESS THAN (TO_DAYS('2026-01-01')),
        PARTITION p_2026 VALUES LESS THAN (TO_DAYS('2027-01-01')),
        PARTITION p_2027 VALUES LESS THAN (TO_DAYS('2028-01-01')),
        PARTITION p_max VALUES LESS THAN MAXVALUE
    )
    """,

    # ── 4. User Stats ─────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id BIGINT UNSIGNED NOT NULL,
        guild_id BIGINT UNSIGNED NOT NULL,
        total_tracks_requested INT UNSIGNED NOT NULL DEFAULT 0,
        total_listening_time BIGINT UNSIGNED NOT NULL DEFAULT 0,
        favorite_tracks JSON NULL,
        last_active DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        PRIMARY KEY (user_id, guild_id),
        INDEX idx_stats_guild_tracks (guild_id, total_tracks_requested DESC),
        INDEX idx_stats_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 5. Personal Favorites ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS favorites (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT UNSIGNED NOT NULL,
        guild_id BIGINT UNSIGNED NOT NULL,
        name VARCHAR(255) NOT NULL,
        track_data JSON NOT NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        UNIQUE KEY uk_user_guild_name (user_id, guild_id, name),
        INDEX idx_favorites_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 6. Multi-Playlist Management (GEN 5.0 NEW) ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS user_playlists (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT UNSIGNED NOT NULL,
        guild_id BIGINT UNSIGNED NOT NULL,
        name VARCHAR(100) NOT NULL,
        description VARCHAR(255) NULL,
        scope VARCHAR(16) NOT NULL DEFAULT 'personal',
        is_public BOOLEAN NOT NULL DEFAULT FALSE,
        play_count INT UNSIGNED NOT NULL DEFAULT 0,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE KEY uk_user_playlist_name (user_id, name),
        INDEX idx_playlists_guild_public (guild_id, is_public)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS playlist_tracks (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        playlist_id BIGINT UNSIGNED NOT NULL,
        position INT UNSIGNED NOT NULL,
        track_data JSON NOT NULL,
        added_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        CONSTRAINT fk_playlist_tracks FOREIGN KEY (playlist_id) REFERENCES user_playlists(id) ON DELETE CASCADE,
        INDEX idx_playlist_pos (playlist_id, position)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 7. Scheduled Playback Tasks (GEN 5.0 NEW) ─────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        voice_channel_id BIGINT UNSIGNED NOT NULL,
        text_channel_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        scheduled_time TIME NOT NULL,
        days_of_week VARCHAR(32) NOT NULL DEFAULT 'daily',
        query VARCHAR(500) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        last_run_at DATETIME(3) NULL,
        locked_by VARCHAR(64) NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_scheduled_active (is_active, scheduled_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 8. Session State Recovery (GEN 5.0 NEW) ───────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS session_state (
        guild_id BIGINT UNSIGNED PRIMARY KEY,
        channel_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
        text_channel_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
        now_playing JSON NULL,
        elapsed_secs INT UNSIGNED NOT NULL DEFAULT 0,
        queue_json JSON NOT NULL,
        loop_mode VARCHAR(16) NOT NULL DEFAULT 'off',
        volume FLOAT NOT NULL DEFAULT 1.0,
        effects_json JSON NOT NULL,
        saved_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        last_heartbeat DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 9. Analytics Buffer & Aggregation ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS analytics (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        payload JSON NULL,
        ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_analytics_guild_ts (guild_id, ts),
        INDEX idx_analytics_event (event_type),
        INDEX idx_analytics_ts (ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 10. Search History (Autocomplete Cache) ───────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS search_history (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        query VARCHAR(200) NOT NULL,
        used_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_search_history_guild (guild_id, used_at DESC),
        INDEX idx_search_history_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

# ── Legacy SQLite Schema (for Dual-Write / Rollback / Unit Tests) ─────────────
_SQLITE_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous   = NORMAL;
PRAGMA foreign_keys  = ON;

CREATE TABLE IF NOT EXISTS queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    track_data TEXT    NOT NULL,
    position   INTEGER NOT NULL,
    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    track_data      TEXT    NOT NULL,
    played_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_played INTEGER DEFAULT 0,
    skipped         BOOLEAN DEFAULT FALSE,
    completed       BOOLEAN DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS server_configs (
    guild_id    INTEGER PRIMARY KEY,
    config_data TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_stats (
    user_id                INTEGER NOT NULL,
    guild_id               INTEGER NOT NULL,
    total_tracks_requested INTEGER DEFAULT 0,
    total_listening_time   INTEGER DEFAULT 0,
    favorite_tracks        TEXT    DEFAULT '[]',
    last_active            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, guild_id)
);
CREATE TABLE IF NOT EXISTS search_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    query    TEXT    NOT NULL,
    used_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS analytics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    event_type TEXT    NOT NULL,
    payload    TEXT    DEFAULT '{}',
    ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    guild_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    track_data TEXT    NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, guild_id, name)
);
CREATE TABLE IF NOT EXISTS queue_bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    tracks_json TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, guild_id, name)
);
CREATE TABLE IF NOT EXISTS user_playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT    NULL,
    scope       TEXT    NOT NULL DEFAULT 'personal',
    is_public   BOOLEAN NOT NULL DEFAULT FALSE,
    play_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    track_data  TEXT    NOT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(playlist_id) REFERENCES user_playlists(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    voice_channel_id INTEGER NOT NULL,
    text_channel_id  INTEGER NOT NULL,
    user_id          INTEGER NOT NULL,
    scheduled_time   TEXT    NOT NULL,
    days_of_week     TEXT    NOT NULL DEFAULT 'daily',
    query            TEXT    NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at      TIMESTAMP NULL,
    locked_by        TEXT    NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS session_state (
    guild_id        INTEGER PRIMARY KEY,
    channel_id      INTEGER NOT NULL DEFAULT 0,
    text_channel_id INTEGER NOT NULL DEFAULT 0,
    now_playing     TEXT,
    elapsed_secs    INTEGER NOT NULL DEFAULT 0,
    queue_json      TEXT    NOT NULL DEFAULT '[]',
    loop_mode       TEXT    NOT NULL DEFAULT 'off',
    volume          REAL    NOT NULL DEFAULT 1.0,
    effects_json    TEXT    NOT NULL DEFAULT '[]',
    saved_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ── Phase 3 (Gen 6.0): Sharding & Master-Replica Router ───────────────────────

class ShardRouter:
    """
    Manages MySQL 8.0+ connection pools across:
      - Primary Master Pool
      - Optional Read-Replica Pool (Read/Write splitting)
      - Optional Distributed Guild Shards (guild_id % num_shards routing)
    """

    def __init__(self) -> None:
        self.master_pool: Optional[aiomysql.Pool] = None
        self.replica_pool: Optional[aiomysql.Pool] = None
        self.shard_pools: list[aiomysql.Pool] = []

    def select_pool(
        self,
        guild_id: Optional[int] = None,
        readonly: bool = False,
    ) -> Optional[aiomysql.Pool]:
        """Select the optimal connection pool for the given guild and operation."""
        if guild_id is not None and self.shard_pools:
            idx = int(guild_id) % len(self.shard_pools)
            return self.shard_pools[idx]
        if readonly and self.replica_pool is not None:
            return self.replica_pool
        return self.master_pool

    def all_pools(self) -> list[aiomysql.Pool]:
        pools: list[aiomysql.Pool] = []
        if self.master_pool:
            pools.append(self.master_pool)
        if self.replica_pool and self.replica_pool not in pools:
            pools.append(self.replica_pool)
        for sp in self.shard_pools:
            if sp not in pools:
                pools.append(sp)
        return pools

    def pool_metrics(self) -> dict[str, Any]:
        """Return live connection pool statistics."""
        if not self.master_pool:
            return {"status": "uninitialised", "size": 0, "freesize": 0, "maxsize": 0}
        used = self.master_pool.size - self.master_pool.freesize
        return {
            "status":       "connected",
            "size":         self.master_pool.size,
            "freesize":     self.master_pool.freesize,
            "used":         used,
            "minsize":      self.master_pool.minsize,
            "maxsize":      self.master_pool.maxsize,
            "has_replica":  self.replica_pool is not None,
            "shard_count":  max(1, len(self.shard_pools)),
        }


def _extract_mysql_errno(exc: BaseException) -> Optional[int]:
    """Extract numeric MySQL error code from pymysql/aiomysql exceptions."""
    if isinstance(exc, pymysql.err.MySQLError) and exc.args:
        code = exc.args[0]
        if isinstance(code, int):
            return code
    return None


def _parse_json_col(val: Any, default: Any = None) -> Any:
    """Safely parse a MySQL JSON column (which may be str, bytes, dict, or list)."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", errors="replace")
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default
    return default


def _format_time_col(val: Any) -> str:
    """Normalise MySQL TIME (timedelta or time or str) to 'HH:MM:SS'."""
    if isinstance(val, dt.timedelta):
        total_secs = int(val.total_seconds()) % 86400
        h, rem = divmod(total_secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    if isinstance(val, dt.time):
        return val.strftime("%H:%M:%S")
    return str(val or "00:00:00")


class DatabaseManager:
    """
    Enterprise Async MySQL 8.0+ Manager with Connection Pooling, Self-Healing,
    Deadlock Retry, Sharding/Replica Routing, and Zero-Downtime Dual-Write Migration.
    """

    MAX_BOOKMARKS_PER_USER = 20

    def __init__(
        self,
        db_path: str = config.DATABASE_PATH,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        db_name: Optional[str] = None,
        pool_min: Optional[int] = None,
        pool_max: Optional[int] = None,
        migration_mode: Optional[str] = None,
    ) -> None:
        self._db_path = db_path
        self._host = host if host is not None else config.DB_HOST
        self._port = int(port if port is not None else config.DB_PORT)
        self._user = user if user is not None else config.DB_USER
        self._password = password if password is not None else config.DB_PASSWORD
        self._db_name = db_name if db_name is not None else config.DB_NAME
        self._pool_min = int(pool_min if pool_min is not None else config.DB_POOL_MIN_SIZE)
        self._pool_max = int(pool_max if pool_max is not None else config.DB_POOL_MAX_SIZE)
        self._charset = config.DB_CHARSET
        self._autocommit = False  # Controlled via explicit transactions per spec
        self._deadlock_retries = int(getattr(config, "DB_DEADLOCK_RETRIES", 3))

        # If initialised with ":memory:", automatically run in sqlite_fallback mode for isolated tests
        if db_path == ":memory:" and migration_mode is None:
            self._migration_mode = "sqlite_fallback"
        else:
            self._migration_mode = (migration_mode or config.DB_MIGRATION_MODE or "mysql").lower()

        # ── Phase 1–3 MySQL Router & Pools ────────────────────────────────────
        self.router = ShardRouter()

        # ── Legacy SQLite Connection (for dual_write / sqlite_fallback) ───────
        self._sqlite_conn: Optional[aiosqlite.Connection] = None
        self._sqlite_lock: asyncio.Lock = asyncio.Lock()

        # ── Perf-1: Analytics write buffer ────────────────────────────────────
        self._analytics_buf: list[tuple[int, str, str]] = []
        self._analytics_lock: asyncio.Lock = asyncio.Lock()
        self._analytics_flush_task: Optional[asyncio.Task] = None

        # ── Perf-1: Server config cache ───────────────────────────────────────
        self._cfg_cache: dict[int, tuple[ServerConfig, float]] = {}
        self._cfg_lock: asyncio.Lock = asyncio.Lock()
        self._cfg_hits: int = 0
        self._cfg_misses: int = 0

        # ── Self-Healing & Dual-Write Diagnostics ─────────────────────────────
        self._reconnect_count: int = 0
        self._deadlock_retry_count: int = 0
        self._dual_write_diffs: int = 0

    @property
    def mode(self) -> str:
        """Current database mode ('mysql', 'dual_write', or 'sqlite_fallback')."""
        return self._migration_mode

    def set_migration_mode(self, new_mode: str) -> None:
        """Live feature-flag switch for Cutover ('mysql') or Rollback ('sqlite_fallback')."""
        valid = {"mysql", "dual_write", "sqlite_fallback"}
        new_mode = new_mode.strip().lower()
        if new_mode not in valid:
            raise ValueError(f"Invalid migration mode: {new_mode!r}. Must be one of {valid}")
        old = self._migration_mode
        self._migration_mode = new_mode
        logger.warning("🔄 Database migration mode switched: %s ➔ %s", old, new_mode)

    @property
    def is_mysql_active(self) -> bool:
        return self._migration_mode in ("mysql", "dual_write") and self.router.master_pool is not None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Initialise MySQL 8.0+ connection pool(s), schema DDL, and optional SQLite dual-write."""
        if self._migration_mode in ("mysql", "dual_write"):
            try:
                await self._init_mysql_pools()
                await self._init_mysql_schema()
                await self.ensure_history_partitions()
                logger.info(
                    "✅ MySQL 8.0+ Pool initialised (%s:%d/%s, min=%d, max=%d, mode=%s)",
                    self._host, self._port, self._db_name,
                    self._pool_min, self._pool_max, self._migration_mode,
                )
            except Exception as exc:
                if self._migration_mode == "dual_write":
                    logger.error(
                        "⚠️ MySQL init failed in dual_write mode (%s); falling back to sqlite_fallback.",
                        exc,
                    )
                    self._migration_mode = "sqlite_fallback"
                else:
                    raise

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            await self._init_sqlite()

        # Start background analytics flush loop
        if not self._analytics_flush_task or self._analytics_flush_task.done():
            self._analytics_flush_task = asyncio.create_task(
                self._analytics_flush_loop(),
                name="db_analytics_flush",
            )

    async def _init_mysql_pools(self) -> None:
        """Create database if missing, then open Master, Replica, and Shard pools."""
        # Step 1: Ensure target database exists with utf8mb4
        bootstrap_conn = await aiomysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            charset=self._charset,
            autocommit=True,
            connect_timeout=config.DB_CONNECT_TIMEOUT,
        )
        try:
            async with bootstrap_conn.cursor() as cur:
                safe_db = self._db_name.replace("`", "")
                await cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{safe_db}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            bootstrap_conn.close()

        # Step 2: Open Master Pool (autocommit=False per spec)
        self.router.master_pool = await aiomysql.create_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._db_name,
            minsize=self._pool_min,
            maxsize=self._pool_max,
            charset=self._charset,
            autocommit=False,
            cursorclass=aiomysql.DictCursor,
            connect_timeout=config.DB_CONNECT_TIMEOUT,
            init_command="SET SESSION time_zone = '+00:00', NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
        )

        # Step 3: Phase 3 Optional Read-Replica Pool
        if config.DB_REPLICA_HOST:
            try:
                self.router.replica_pool = await aiomysql.create_pool(
                    host=config.DB_REPLICA_HOST,
                    port=config.DB_REPLICA_PORT,
                    user=config.DB_REPLICA_USER,
                    password=config.DB_REPLICA_PASSWORD,
                    db=self._db_name,
                    minsize=max(2, self._pool_min // 2),
                    maxsize=self._pool_max,
                    charset=self._charset,
                    autocommit=True,
                    cursorclass=aiomysql.DictCursor,
                    connect_timeout=config.DB_CONNECT_TIMEOUT,
                    init_command="SET SESSION time_zone = '+00:00', NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
                )
                logger.info("✅ MySQL Read-Replica Pool connected at %s:%d", config.DB_REPLICA_HOST, config.DB_REPLICA_PORT)
            except Exception as exc:
                logger.warning("⚠️ Could not connect Read-Replica Pool (%s); routing reads to Master.", exc)

        # Step 4: Phase 3 Optional Distributed Guild Shards
        try:
            shards_cfg = json.loads(config.DB_SHARDS_JSON or "[]")
            if isinstance(shards_cfg, list) and shards_cfg:
                for idx, sc in enumerate(shards_cfg):
                    sp = await aiomysql.create_pool(
                        host=sc.get("host", self._host),
                        port=int(sc.get("port", self._port)),
                        user=sc.get("user", self._user),
                        password=sc.get("password", self._password),
                        db=sc.get("db", self._db_name),
                        minsize=int(sc.get("minsize", self._pool_min)),
                        maxsize=int(sc.get("maxsize", self._pool_max)),
                        charset=self._charset,
                        autocommit=False,
                        cursorclass=aiomysql.DictCursor,
                        connect_timeout=config.DB_CONNECT_TIMEOUT,
                    )
                    self.router.shard_pools.append(sp)
                logger.info("✅ Initialised %d MySQL Shard Pool(s)", len(self.router.shard_pools))
        except Exception as exc:
            logger.warning("⚠️ Shard config parse/init error: %s", exc)

    async def _init_mysql_schema(self) -> None:
        """Apply all Enterprise Normalized DDL statements across Master (and Shard) pools."""
        target_pools = self.router.shard_pools if self.router.shard_pools else ([self.router.master_pool] if self.router.master_pool else [])
        for pool in target_pools:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for ddl in MYSQL_SCHEMA_DDL:
                        await cur.execute(ddl)
                await conn.commit()

    async def _init_sqlite(self) -> None:
        """Initialise legacy SQLite database for dual_write, fallback, or unit tests."""
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_conn = await aiosqlite.connect(self._db_path)
        self._sqlite_conn.row_factory = aiosqlite.Row
        await self._sqlite_conn.executescript(_SQLITE_SCHEMA_SQL)
        await self._sqlite_conn.commit()
        logger.info("SQLite engine initialised at %s (mode=%s)", self._db_path, self._migration_mode)

    @classmethod
    async def create_standalone_pool(cls) -> "DatabaseManager":
        """
        Phase 2 (Gen 5.x): Create an independent DatabaseManager pool for
        standalone Web Dashboard or background worker processes.
        """
        db = cls()
        await db.initialise()
        return db

    async def close(self) -> None:
        """Gracefully flush analytics buffer and close all MySQL pools and SQLite connections."""
        if self._analytics_flush_task and not self._analytics_flush_task.done():
            self._analytics_flush_task.cancel()
            try:
                await self._analytics_flush_task
            except asyncio.CancelledError:
                pass

        await self._flush_analytics_buffer()

        # Close all MySQL pools
        for pool in self.router.all_pools():
            try:
                pool.close()
                await pool.wait_closed()
            except Exception as exc:
                logger.warning("MySQL pool close warning: %s", exc)
        self.router.master_pool = None
        self.router.replica_pool = None
        self.router.shard_pools.clear()

        # Close SQLite fallback if open
        if self._sqlite_conn:
            try:
                await self._sqlite_conn.commit()
                await self._sqlite_conn.close()
            except Exception as exc:
                logger.warning("SQLite close warning: %s", exc)
            finally:
                self._sqlite_conn = None

    # ── Self-Healing & Deadlock Retry Engine ──────────────────────────────────

    async def _run_mysql(
        self,
        op: Callable[[aiomysql.Connection, aiomysql.DictCursor], Coroutine[Any, Any, T]],
        *,
        guild_id: Optional[int] = None,
        readonly: bool = False,
    ) -> T:
        """
        Execute a database operation on a pooled MySQL connection with:
          1. Auto-Ping health check (`conn.ping(reconnect=True)`)
          2. Automatic recovery from Error 2006 (MySQL server has gone away) & 2013
          3. Automatic transaction COMMIT / ROLLBACK (`autocommit=False`)
          4. Automatic Deadlock (Error 1213) & Lock Wait Timeout (Error 1205) retry
        """
        pool = self.router.select_pool(guild_id=guild_id, readonly=readonly)
        if pool is None:
            raise RuntimeError("MySQL connection pool is not initialised")

        max_attempts = self._deadlock_retries + 1
        last_exc: Optional[BaseException] = None

        for attempt in range(max_attempts):
            conn: Optional[aiomysql.Connection] = None
            try:
                conn = await pool.acquire()
                # Self-Healing Auto-Ping: ensures connection is alive before query
                await conn.ping(reconnect=True)
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    result = await op(conn, cur)
                if not readonly:
                    await conn.commit()
                else:
                    # End consistent read snapshot cleanly when autocommit=False
                    await conn.commit()
                return result

            except Exception as exc:
                last_exc = exc
                errno = _extract_mysql_errno(exc)

                if conn is not None:
                    try:
                        await conn.rollback()
                    except Exception:
                        pass

                # Case 1: Connection lost / MySQL Server has gone away (2006, 2013)
                if errno in MYSQL_CONN_LOST_CODES or isinstance(exc, (ConnectionResetError, BrokenPipeError, EOFError)):
                    self._reconnect_count += 1
                    logger.warning(
                        "♻️ MySQL connection lost (errno=%s, attempt=%d/%d) — healing connection from pool…",
                        errno, attempt + 1, max_attempts,
                    )
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue

                # Case 2: InnoDB Deadlock (1213) or Lock Wait Timeout (1205)
                if errno in MYSQL_DEADLOCK_CODES and attempt + 1 < max_attempts:
                    self._deadlock_retry_count += 1
                    backoff = (0.05 * (2 ** attempt)) + random.uniform(0.01, 0.05)
                    logger.warning(
                        "🔁 MySQL deadlock/lock-timeout detected (errno=%d, retry=%d/%d in %.2fs)",
                        errno, attempt + 1, self._deadlock_retries, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                raise

            finally:
                if conn is not None:
                    try:
                        pool.release(conn)
                    except Exception:
                        pass

        assert last_exc is not None
        raise last_exc

    @asynccontextmanager
    async def _sqlite_connect(self):
        """Yield the SQLite connection under lock (for dual_write / fallback / unit tests)."""
        if self._sqlite_conn:
            async with self._sqlite_lock:
                yield self._sqlite_conn
        else:
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            try:
                yield conn
            finally:
                await conn.close()

    # ── Health / Latency / Pool Stats ─────────────────────────────────────────

    async def ping(self, timeout: float = 5.0) -> float:
        """
        Run a lightweight ``SELECT 1`` and return latency in milliseconds.
        Used by MetricsCollector, SelfTest, and /health.
        """
        t0 = time.monotonic()
        if self.is_mysql_active:
            async def _ping_op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute("SELECT 1")
                await cur.fetchone()

            await asyncio.wait_for(self._run_mysql(_ping_op, readonly=True), timeout=timeout)
        else:
            async with self._sqlite_connect() as conn:
                await asyncio.wait_for(conn.execute("SELECT 1"), timeout=timeout)
        return (time.monotonic() - t0) * 1000.0

    def pool_stats(self) -> dict[str, Any]:
        """Return pool & resilience diagnostics for /health and metrics."""
        base = self.router.pool_metrics()
        base.update({
            "mode":                 self._migration_mode,
            "reconnect_count":      self._reconnect_count,
            "deadlock_retry_count": self._deadlock_retry_count,
            "dual_write_diffs":     self._dual_write_diffs,
        })
        return base

    # ── History Partition Auto-Management (MySQL 8.0) ─────────────────────────

    async def ensure_history_partitions(self) -> None:
        """
        Ensure the `history` table has yearly range partitions for the current
        and next calendar year before `p_max` so inserts never fail.
        """
        if not self.is_mysql_active:
            return
        now_year = datetime.now(timezone.utc).year
        target_years = [now_year, now_year + 1]

        async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
            await cur.execute(
                """
                SELECT PARTITION_NAME
                FROM INFORMATION_SCHEMA.PARTITIONS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'history'
                  AND PARTITION_NAME IS NOT NULL
                """,
                (self._db_name,),
            )
            rows = await cur.fetchall()
            existing = {r["PARTITION_NAME"] for r in rows}
            if not existing or "p_max" not in existing:
                return

            for yr in target_years:
                part_name = f"p_{yr}"
                next_yr_date = f"{yr + 1}-01-01"
                if part_name not in existing:
                    logger.info("Creating MySQL history partition %s (< %s)", part_name, next_yr_date)
                    await cur.execute(
                        f"""
                        ALTER TABLE history REORGANIZE PARTITION p_max INTO (
                            PARTITION {part_name} VALUES LESS THAN (TO_DAYS('{next_yr_date}')),
                            PARTITION p_max VALUES LESS THAN MAXVALUE
                        )
                        """
                    )

        try:
            await self._run_mysql(_op)
        except Exception as exc:
            logger.debug("History partition check skipped/warning: %s", exc)

    # ── 1. Server Config (Normalized 20-Column Design) ────────────────────────

    async def get_server_config(self, guild_id: int) -> ServerConfig:
        """
        Return ServerConfig for the guild.
        Checks in-memory LRU cache first (TTL = 30s).
        """
        now = time.monotonic()

        async with self._cfg_lock:
            entry = self._cfg_cache.get(guild_id)
            if entry:
                cfg, ts = entry
                if now - ts < _CFG_CACHE_TTL:
                    self._cfg_hits += 1
                    return cfg
            self._cfg_misses += 1

        row: Optional[dict[str, Any]] = None
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> Optional[dict]:
                await cur.execute(
                    "SELECT * FROM server_configs WHERE guild_id = %s",
                    (guild_id,),
                )
                return await cur.fetchone()

            row = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "SELECT config_data FROM server_configs WHERE guild_id = ?",
                    (guild_id,),
                )
                r = await cursor.fetchone()
                row = dict(r) if r else None

        cfg = ServerConfig.from_db_row(row) if row else ServerConfig.default(guild_id)

        async with self._cfg_lock:
            self._cfg_cache[guild_id] = (cfg, time.monotonic())

        try:
            from core.i18n import set_locale_cache
            set_locale_cache(guild_id, getattr(cfg, "language", "en"))
        except Exception:
            pass

        return cfg

    def invalidate_config_cache(self, guild_id: int) -> None:
        """Invalidate cached config for a guild after a save."""
        self._cfg_cache.pop(guild_id, None)

    def config_cache_stats(self) -> dict:
        """Return hit/miss metrics for the config cache."""
        total = self._cfg_hits + self._cfg_misses
        return {
            "hits":     self._cfg_hits,
            "misses":   self._cfg_misses,
            "hit_rate": round(self._cfg_hits / total, 3) if total else 0.0,
            "size":     len(self._cfg_cache),
        }

    async def save_server_config(self, cfg: ServerConfig) -> None:
        """Upsert normalized ServerConfig into MySQL (and SQLite if dual_write)."""
        if self.is_mysql_active:
            params = cfg.to_db_params()

            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute(
                    """
                    INSERT INTO server_configs (
                        guild_id, volume, audio_quality, max_track_len, idle_timeout,
                        dj_only, dj_role_id, request_channel_id, timezone,
                        auto_playlist, auto_playlist_size, smart_autoplay,
                        queue_locked, queue_add_permission, duplicate_mode,
                        auto_leave_alone, auto_pause_empty, embed_theme,
                        language, guild_presets
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    ) AS new_cfg
                    ON DUPLICATE KEY UPDATE
                        volume               = new_cfg.volume,
                        audio_quality        = new_cfg.audio_quality,
                        max_track_len        = new_cfg.max_track_len,
                        idle_timeout         = new_cfg.idle_timeout,
                        dj_only              = new_cfg.dj_only,
                        dj_role_id           = new_cfg.dj_role_id,
                        request_channel_id   = new_cfg.request_channel_id,
                        timezone             = new_cfg.timezone,
                        auto_playlist        = new_cfg.auto_playlist,
                        auto_playlist_size   = new_cfg.auto_playlist_size,
                        smart_autoplay       = new_cfg.smart_autoplay,
                        queue_locked         = new_cfg.queue_locked,
                        queue_add_permission = new_cfg.queue_add_permission,
                        duplicate_mode       = new_cfg.duplicate_mode,
                        auto_leave_alone     = new_cfg.auto_leave_alone,
                        auto_pause_empty     = new_cfg.auto_pause_empty,
                        embed_theme          = new_cfg.embed_theme,
                        language             = new_cfg.language,
                        guild_presets        = new_cfg.guild_presets,
                        updated_at           = CURRENT_TIMESTAMP(3)
                    """,
                    params,
                )

            await self._run_mysql(_op, guild_id=cfg.guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO server_configs (guild_id, config_data, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        config_data = excluded.config_data,
                        updated_at  = CURRENT_TIMESTAMP
                    """,
                    (cfg.guild_id, cfg.to_json()),
                )
                await conn.commit()

        self.invalidate_config_cache(cfg.guild_id)
        try:
            from core.i18n import set_locale_cache
            set_locale_cache(cfg.guild_id, getattr(cfg, "language", "en"))
        except Exception:
            pass

    # ── 2. Active Queue ───────────────────────────────────────────────────────

    async def save_queue(
        self,
        guild_id: int,
        channel_id: int,
        tracks: list[Track],
    ) -> None:
        """Persist the full queue for a guild in a single atomic transaction."""
        rows = [
            (guild_id, channel_id, track.to_json(), pos, track.requested_by_id or 0)
            for pos, track in enumerate(tracks)
        ]

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute("DELETE FROM queue WHERE guild_id = %s", (guild_id,))
                if rows:
                    await cur.executemany(
                        """
                        INSERT INTO queue (guild_id, channel_id, track_data, position, user_id)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        rows,
                    )

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute("DELETE FROM queue WHERE guild_id = ?", (guild_id,))
                if rows:
                    await conn.executemany(
                        """
                        INSERT INTO queue (guild_id, channel_id, track_data, position, user_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                await conn.commit()

    async def load_queue(self, guild_id: int) -> list[Track]:
        """Load persisted queue for a guild, ordered by position."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    "SELECT track_data FROM queue WHERE guild_id = %s ORDER BY position ASC",
                    (guild_id,),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "SELECT track_data FROM queue WHERE guild_id = ? ORDER BY position ASC",
                    (guild_id,),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        return [Track.from_json(row["track_data"]) for row in rows]

    async def clear_queue(self, guild_id: int) -> None:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute("DELETE FROM queue WHERE guild_id = %s", (guild_id,))

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute("DELETE FROM queue WHERE guild_id = ?", (guild_id,))
                await conn.commit()

    # ── 3 & 4. Play History & User Stats ──────────────────────────────────────

    async def record_track_played(
        self,
        guild_id:        int,
        user_id:         int,
        track:           Track,
        duration_played: int  = 0,
        skipped:         bool = False,
        completed:       bool = False,
    ) -> None:
        """Log a track play into `history` and update `user_stats` atomically."""
        track_json = track.to_json()

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute(
                    """
                    INSERT INTO history (guild_id, user_id, track_data, duration_played, skipped, completed)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (guild_id, user_id, track_json, int(duration_played), bool(skipped), bool(completed)),
                )
                await cur.execute(
                    """
                    INSERT INTO user_stats (
                        user_id, guild_id, total_tracks_requested, total_listening_time, last_active
                    )
                    VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP(3)) AS new_stat
                    ON DUPLICATE KEY UPDATE
                        total_tracks_requested = user_stats.total_tracks_requested + 1,
                        total_listening_time   = user_stats.total_listening_time + new_stat.total_listening_time,
                        last_active            = CURRENT_TIMESTAMP(3)
                    """,
                    (user_id, guild_id, int(duration_played)),
                )

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO history (guild_id, user_id, track_data, duration_played, skipped, completed)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, track_json, duration_played, skipped, completed),
                )
                await conn.execute(
                    """
                    INSERT INTO user_stats (user_id, guild_id, total_tracks_requested, total_listening_time, last_active)
                    VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, guild_id) DO UPDATE SET
                        total_tracks_requested = total_tracks_requested + 1,
                        total_listening_time   = total_listening_time + excluded.total_listening_time,
                        last_active            = CURRENT_TIMESTAMP
                    """,
                    (user_id, guild_id, duration_played),
                )
                await conn.commit()

    async def get_history(
        self,
        guild_id: int,
        limit: int = 20,
        user_id: Optional[int] = None,
    ) -> list[dict]:
        """Return recent play history rows as dicts."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                if user_id:
                    await cur.execute(
                        """
                        SELECT track_data, played_at, skipped, completed, user_id
                        FROM history
                        WHERE guild_id = %s AND user_id = %s
                        ORDER BY played_at DESC LIMIT %s
                        """,
                        (guild_id, user_id, int(limit)),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT track_data, played_at, skipped, completed, user_id
                        FROM history
                        WHERE guild_id = %s
                        ORDER BY played_at DESC LIMIT %s
                        """,
                        (guild_id, int(limit)),
                    )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
            for r in rows:
                if isinstance(r.get("played_at"), datetime):
                    r["played_at"] = r["played_at"].strftime("%Y-%m-%d %H:%M:%S")
            return rows

        async with self._sqlite_connect() as conn:
            if user_id:
                cursor = await conn.execute(
                    """
                    SELECT track_data, played_at, skipped, completed, user_id
                    FROM history WHERE guild_id = ? AND user_id = ?
                    ORDER BY played_at DESC LIMIT ?
                    """,
                    (guild_id, user_id, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT track_data, played_at, skipped, completed, user_id
                    FROM history WHERE guild_id = ?
                    ORDER BY played_at DESC LIMIT ?
                    """,
                    (guild_id, limit),
                )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_tracks_for_autoplaylist(
        self, guild_id: int, limit: int = 20
    ) -> list[Track]:
        """Return distinct recently-played tracks for auto-playlist seeding."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT track_data FROM history
                    WHERE guild_id = %s AND skipped = FALSE AND completed = TRUE
                    ORDER BY played_at DESC LIMIT %s
                    """,
                    (guild_id, int(limit)),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT track_data FROM history
                    WHERE guild_id = ? AND skipped = FALSE AND completed = TRUE
                    ORDER BY played_at DESC LIMIT ?
                    """,
                    (guild_id, limit),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        seen_urls: set[str] = set()
        tracks: list[Track] = []
        for row in rows:
            t = Track.from_json(row["track_data"])
            if t.url not in seen_urls:
                seen_urls.add(t.url)
                tracks.append(t)
        return tracks

    async def get_user_stats(
        self, guild_id: int, user_id: int
    ) -> Optional[dict]:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> Optional[dict]:
                await cur.execute(
                    "SELECT * FROM user_stats WHERE user_id = %s AND guild_id = %s",
                    (user_id, guild_id),
                )
                return await cur.fetchone()

            row = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
            if row and isinstance(row.get("last_active"), datetime):
                row["last_active"] = row["last_active"].strftime("%Y-%m-%d %H:%M:%S")
            return dict(row) if row else None

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM user_stats WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_top_users(self, guild_id: int, limit: int = 5) -> list[dict]:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT user_id, total_tracks_requested, total_listening_time
                    FROM user_stats WHERE guild_id = %s
                    ORDER BY total_tracks_requested DESC LIMIT %s
                    """,
                    (guild_id, int(limit)),
                )
                return list(await cur.fetchall())

            return await self._run_mysql(_op, guild_id=guild_id, readonly=True)

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                """
                SELECT user_id, total_tracks_requested, total_listening_time
                FROM user_stats WHERE guild_id = ?
                ORDER BY total_tracks_requested DESC LIMIT ?
                """,
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Search History (Autocomplete) ─────────────────────────────────────────

    async def add_search_history(
        self, guild_id: int, user_id: int, query: str
    ) -> None:
        clean_q = query.strip()[:200]
        if not clean_q:
            return

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute(
                    "INSERT INTO search_history (guild_id, user_id, query) VALUES (%s, %s, %s)",
                    (guild_id, user_id, clean_q),
                )
                await cur.execute(
                    """
                    DELETE FROM search_history
                    WHERE guild_id = %s AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM search_history
                            WHERE guild_id = %s
                            ORDER BY used_at DESC LIMIT 500
                        ) AS recent_sh
                    )
                    """,
                    (guild_id, guild_id),
                )

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute(
                    "INSERT INTO search_history (guild_id, user_id, query) VALUES (?, ?, ?)",
                    (guild_id, user_id, clean_q),
                )
                await conn.execute(
                    """
                    DELETE FROM search_history WHERE guild_id = ? AND id NOT IN (
                        SELECT id FROM search_history WHERE guild_id = ?
                        ORDER BY used_at DESC LIMIT 500
                    )
                    """,
                    (guild_id, guild_id),
                )
                await conn.commit()

    async def get_search_history(
        self, guild_id: int, prefix: str = "", limit: int = 25
    ) -> list[str]:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                if prefix:
                    await cur.execute(
                        """
                        SELECT query, MAX(used_at) AS latest_used
                        FROM search_history
                        WHERE guild_id = %s AND query LIKE %s
                        GROUP BY query
                        ORDER BY latest_used DESC LIMIT %s
                        """,
                        (guild_id, f"{prefix}%", int(limit)),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT query, MAX(used_at) AS latest_used
                        FROM search_history
                        WHERE guild_id = %s
                        GROUP BY query
                        ORDER BY latest_used DESC LIMIT %s
                        """,
                        (guild_id, int(limit)),
                    )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
            return [r["query"] for r in rows]

        async with self._sqlite_connect() as conn:
            if prefix:
                cursor = await conn.execute(
                    """
                    SELECT DISTINCT query FROM search_history
                    WHERE guild_id = ? AND query LIKE ?
                    ORDER BY used_at DESC LIMIT ?
                    """,
                    (guild_id, f"{prefix}%", limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT DISTINCT query FROM search_history
                    WHERE guild_id = ?
                    ORDER BY used_at DESC LIMIT ?
                    """,
                    (guild_id, limit),
                )
            rows = await cursor.fetchall()
        return [row["query"] for row in rows]

    # ── 5. Personal Favorites ─────────────────────────────────────────────────

    async def add_favorite(
        self, user_id: int, guild_id: int, name: str, track: Track
    ) -> bool:
        clean_name = name.strip()[:255]
        track_json = track.to_json()

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> bool:
                await cur.execute(
                    "SELECT COUNT(*) AS cnt FROM favorites WHERE user_id = %s AND guild_id = %s",
                    (user_id, guild_id),
                )
                row = await cur.fetchone()
                if row and int(row["cnt"]) >= config.MAX_FAVORITES_PER_USER:
                    return False
                try:
                    await cur.execute(
                        """
                        INSERT INTO favorites (user_id, guild_id, name, track_data)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (user_id, guild_id, clean_name, track_json),
                    )
                    return True
                except pymysql.err.IntegrityError:
                    return False

            ok = await self._run_mysql(_op, guild_id=guild_id)
            if ok and self._migration_mode == "dual_write":
                try:
                    async with self._sqlite_connect() as sconn:
                        await sconn.execute(
                            "INSERT OR IGNORE INTO favorites (user_id, guild_id, name, track_data) VALUES (?, ?, ?, ?)",
                            (user_id, guild_id, clean_name, track_json),
                        )
                        await sconn.commit()
                except Exception:
                    pass
            return ok

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] >= config.MAX_FAVORITES_PER_USER:
                return False
            try:
                await conn.execute(
                    "INSERT INTO favorites (user_id, guild_id, name, track_data) VALUES (?, ?, ?, ?)",
                    (user_id, guild_id, clean_name, track_json),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def remove_favorite(
        self, user_id: int, guild_id: int, name: str
    ) -> bool:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> bool:
                await cur.execute(
                    "DELETE FROM favorites WHERE user_id = %s AND guild_id = %s AND name = %s",
                    (user_id, guild_id, name),
                )
                return cur.rowcount > 0

            removed = await self._run_mysql(_op, guild_id=guild_id)
            if self._migration_mode == "dual_write":
                async with self._sqlite_connect() as sconn:
                    await sconn.execute(
                        "DELETE FROM favorites WHERE user_id = ? AND guild_id = ? AND name = ?",
                        (user_id, guild_id, name),
                    )
                    await sconn.commit()
            return removed

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def get_favorites(
        self, user_id: int, guild_id: int
    ) -> list[dict]:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT name, track_data, created_at
                    FROM favorites WHERE user_id = %s AND guild_id = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id, guild_id),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT name, track_data, created_at
                    FROM favorites WHERE user_id = ? AND guild_id = ?
                    ORDER BY created_at DESC
                    """,
                    (user_id, guild_id),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        result = []
        for row in rows:
            try:
                track = Track.from_json(row["track_data"])
                created = row["created_at"]
                if isinstance(created, datetime):
                    created = created.strftime("%Y-%m-%d %H:%M:%S")
                result.append({
                    "name":       row["name"],
                    "track":      track,
                    "created_at": created,
                })
            except Exception:
                pass
        return result

    async def get_favorite_by_name(
        self, user_id: int, guild_id: int, name: str
    ) -> Optional[Track]:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> Optional[dict]:
                await cur.execute(
                    "SELECT track_data FROM favorites WHERE user_id = %s AND guild_id = %s AND name = %s",
                    (user_id, guild_id, name),
                )
                return await cur.fetchone()

            row = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "SELECT track_data FROM favorites WHERE user_id = ? AND guild_id = ? AND name = ?",
                    (user_id, guild_id, name),
                )
                r = await cursor.fetchone()
                row = dict(r) if r else None

        if row:
            try:
                return Track.from_json(row["track_data"])
            except Exception:
                return None
        return None

    async def count_favorites(self, user_id: int, guild_id: int) -> int:
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> int:
                await cur.execute(
                    "SELECT COUNT(*) AS cnt FROM favorites WHERE user_id = %s AND guild_id = %s",
                    (user_id, guild_id),
                )
                row = await cur.fetchone()
                return int(row["cnt"]) if row else 0

            return await self._run_mysql(_op, guild_id=guild_id, readonly=True)

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    # ── 6. Multi-Playlist Management (GEN 5.0 NEW: user_playlists & playlist_tracks) ──

    async def create_playlist(
        self,
        user_id:     int,
        guild_id:    int,
        name:        str,
        tracks:      list[Track],
        description: Optional[str] = None,
        scope:       str = "personal",
        is_public:   bool = False,
    ) -> bool:
        """
        Create a Gen 5.0 Multi-Playlist and bulk-insert its tracks into `playlist_tracks`.
        Returns True on success, False on name collision or limit reached.
        """
        clean_name = name.strip()[:100]
        clean_desc = description.strip()[:255] if description else None
        clean_scope = scope if scope in ("personal", "guild", "global") else "personal"

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> bool:
                await cur.execute(
                    "SELECT COUNT(*) AS cnt FROM user_playlists WHERE user_id = %s",
                    (user_id,),
                )
                row = await cur.fetchone()
                if row and int(row["cnt"]) >= self.MAX_BOOKMARKS_PER_USER:
                    return False

                try:
                    await cur.execute(
                        """
                        INSERT INTO user_playlists (user_id, guild_id, name, description, scope, is_public)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, guild_id, clean_name, clean_desc, clean_scope, bool(is_public)),
                    )
                    playlist_id = cur.lastrowid
                    if tracks:
                        track_rows = [
                            (playlist_id, pos, t.to_json())
                            for pos, t in enumerate(tracks)
                        ]
                        await cur.executemany(
                            """
                            INSERT INTO playlist_tracks (playlist_id, position, track_data)
                            VALUES (%s, %s, %s)
                            """,
                            track_rows,
                        )
                    return True
                except pymysql.err.IntegrityError:
                    return False

            ok = await self._run_mysql(_op, guild_id=guild_id)
            if ok and self._migration_mode == "dual_write":
                await self._sqlite_save_playlist(user_id, guild_id, clean_name, tracks, clean_desc, clean_scope, is_public)
            return ok

        return await self._sqlite_save_playlist(user_id, guild_id, clean_name, tracks, clean_desc, clean_scope, is_public)

    async def _sqlite_save_playlist(
        self,
        user_id: int,
        guild_id: int,
        clean_name: str,
        tracks: list[Track],
        clean_desc: Optional[str],
        clean_scope: str,
        is_public: bool,
    ) -> bool:
        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM user_playlists WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] >= self.MAX_BOOKMARKS_PER_USER:
                return False
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO user_playlists (user_id, guild_id, name, description, scope, is_public)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, guild_id, clean_name, clean_desc, clean_scope, bool(is_public)),
                )
                playlist_id = cur.lastrowid
                if tracks:
                    track_rows = [(playlist_id, pos, t.to_json()) for pos, t in enumerate(tracks)]
                    await conn.executemany(
                        "INSERT INTO playlist_tracks (playlist_id, position, track_data) VALUES (?, ?, ?)",
                        track_rows,
                    )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def load_playlist(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
        increment_play_count: bool = True,
    ) -> Optional[list[Track]]:
        """
        Load tracks from `user_playlists` + `playlist_tracks`.
        Searches user's own playlists first, then public guild/global playlists.
        """
        clean_name = name.strip()[:100]
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> Optional[list[dict]]:
                await cur.execute(
                    """
                    SELECT id FROM user_playlists
                    WHERE name = %s
                      AND (
                          user_id = %s
                          OR (guild_id = %s AND is_public = TRUE)
                          OR (scope = 'global' AND is_public = TRUE)
                      )
                    ORDER BY (user_id = %s) DESC, play_count DESC
                    LIMIT 1
                    """,
                    (clean_name, user_id, guild_id, user_id),
                )
                pl = await cur.fetchone()
                if not pl:
                    return None
                pl_id = pl["id"]
                if increment_play_count:
                    await cur.execute(
                        "UPDATE user_playlists SET play_count = play_count + 1 WHERE id = %s",
                        (pl_id,),
                    )
                await cur.execute(
                    """
                    SELECT track_data FROM playlist_tracks
                    WHERE playlist_id = %s
                    ORDER BY position ASC
                    """,
                    (pl_id,),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id)
            if rows is None:
                return None
            return [Track.from_json(r["track_data"]) for r in rows]

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                """
                SELECT id FROM user_playlists
                WHERE name = ? AND (user_id = ? OR (guild_id = ? AND is_public = 1) OR (scope = 'global' AND is_public = 1))
                ORDER BY (user_id = ?) DESC LIMIT 1
                """,
                (clean_name, user_id, guild_id, user_id),
            )
            pl = await cursor.fetchone()
            if not pl:
                return None
            if increment_play_count:
                await conn.execute(
                    "UPDATE user_playlists SET play_count = play_count + 1 WHERE id = ?",
                    (pl["id"],),
                )
                await conn.commit()
            cursor = await conn.execute(
                "SELECT track_data FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC",
                (pl["id"],),
            )
            rows = await cursor.fetchall()
            return [Track.from_json(r["track_data"]) for r in rows]

    async def list_playlists(
        self,
        user_id:  int,
        guild_id: int,
        include_public: bool = False,
    ) -> list[dict]:
        """List playlists with track counts from `user_playlists` + `playlist_tracks`."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                if include_public:
                    await cur.execute(
                        """
                        SELECT p.id, p.name, p.description, p.scope, p.is_public,
                               p.play_count, p.created_at, p.user_id,
                               COUNT(pt.id) AS track_count
                        FROM user_playlists p
                        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
                        WHERE p.user_id = %s
                           OR (p.guild_id = %s AND p.is_public = TRUE)
                           OR (p.scope = 'global' AND p.is_public = TRUE)
                        GROUP BY p.id
                        ORDER BY (p.user_id = %s) DESC, p.created_at DESC
                        """,
                        (user_id, guild_id, user_id),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT p.id, p.name, p.description, p.scope, p.is_public,
                               p.play_count, p.created_at, p.user_id,
                               COUNT(pt.id) AS track_count
                        FROM user_playlists p
                        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
                        WHERE p.user_id = %s
                        GROUP BY p.id
                        ORDER BY p.created_at DESC
                        """,
                        (user_id,),
                    )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT p.id, p.name, p.description, p.scope, p.is_public,
                           p.play_count, p.created_at, p.user_id,
                           COUNT(pt.id) AS track_count
                    FROM user_playlists p
                    LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
                    WHERE p.user_id = ?
                    GROUP BY p.id
                    ORDER BY p.created_at DESC
                    """,
                    (user_id,),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        result = []
        for r in rows:
            created = r["created_at"]
            if isinstance(created, datetime):
                created = created.strftime("%Y-%m-%d %H:%M:%S")
            result.append({
                "id":          r["id"],
                "name":        r["name"],
                "description": r.get("description"),
                "scope":       r.get("scope", "personal"),
                "is_public":   bool(r.get("is_public", False)),
                "play_count":  int(r.get("play_count", 0)),
                "count":       int(r.get("track_count", 0)),
                "created_at":  created,
                "user_id":     int(r.get("user_id", user_id)),
            })
        return result

    async def delete_playlist(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
    ) -> bool:
        """Delete a user's playlist by name (cascades to `playlist_tracks`)."""
        clean_name = name.strip()[:100]
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> bool:
                await cur.execute(
                    "DELETE FROM user_playlists WHERE user_id = %s AND name = %s",
                    (user_id, clean_name),
                )
                return cur.rowcount > 0

            deleted = await self._run_mysql(_op, guild_id=guild_id)
            if self._migration_mode == "dual_write":
                async with self._sqlite_connect() as sconn:
                    await sconn.execute(
                        "DELETE FROM user_playlists WHERE user_id = ? AND name = ?",
                        (user_id, clean_name),
                    )
                    await sconn.commit()
            return deleted

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM user_playlists WHERE user_id = ? AND name = ?",
                (user_id, clean_name),
            )
            await conn.commit()
            return cursor.rowcount > 0

    # ── Queue Bookmarks (Mapped directly onto Gen 5.0 Multi-Playlists) ────────

    async def save_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
        tracks:   list[Track],
        *,
        description: Optional[str] = None,
        scope: str = "personal",
        is_public: bool = False,
    ) -> bool:
        """Save a queue snapshot into Gen 5.0 `user_playlists` + `playlist_tracks`."""
        return await self.create_playlist(
            user_id=user_id,
            guild_id=guild_id,
            name=name,
            tracks=tracks,
            description=description,
            scope=scope,
            is_public=is_public,
        )

    async def load_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
    ) -> Optional[list[Track]]:
        """Load a bookmark/playlist by name from `user_playlists` + `playlist_tracks`."""
        return await self.load_playlist(user_id=user_id, guild_id=guild_id, name=name)

    async def list_bookmarks(
        self,
        user_id:  int,
        guild_id: int,
        include_public: bool = False,
    ) -> list[dict]:
        """List all bookmarks/playlists for a user."""
        return await self.list_playlists(user_id=user_id, guild_id=guild_id, include_public=include_public)

    async def delete_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
    ) -> bool:
        """Delete a bookmark/playlist by name."""
        return await self.delete_playlist(user_id=user_id, guild_id=guild_id, name=name)

    # ── 7. Scheduled Playback Tasks (GEN 5.0 NEW: scheduled_tasks) ────────────

    async def create_scheduled_task(
        self,
        guild_id:         int,
        voice_channel_id: int,
        text_channel_id:  int,
        user_id:          int,
        scheduled_time:   str,  # "HH:MM" or "HH:MM:SS"
        query:            str,
        days_of_week:     str = "daily",
    ) -> int:
        """Create a scheduled playback task and return its new ID."""
        if len(scheduled_time) == 5:
            scheduled_time = f"{scheduled_time}:00"
        clean_query = query.strip()[:500]
        clean_days = days_of_week.strip()[:32] or "daily"

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> int:
                await cur.execute(
                    """
                    INSERT INTO scheduled_tasks (
                        guild_id, voice_channel_id, text_channel_id, user_id,
                        scheduled_time, days_of_week, query, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                    """,
                    (guild_id, voice_channel_id, text_channel_id, user_id, scheduled_time, clean_days, clean_query),
                )
                return int(cur.lastrowid)

            return await self._run_mysql(_op, guild_id=guild_id)

        async with self._sqlite_connect() as conn:
            cur = await conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    guild_id, voice_channel_id, text_channel_id, user_id,
                    scheduled_time, days_of_week, query, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (guild_id, voice_channel_id, text_channel_id, user_id, scheduled_time, clean_days, clean_query),
            )
            await conn.commit()
            return int(cur.lastrowid or 0)

    async def list_scheduled_tasks(self, guild_id: int) -> list[dict]:
        """List all scheduled tasks for a guild."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT * FROM scheduled_tasks
                    WHERE guild_id = %s
                    ORDER BY scheduled_time ASC
                    """,
                    (guild_id,),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE guild_id = ? ORDER BY scheduled_time ASC",
                    (guild_id,),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        for r in rows:
            r["scheduled_time"] = _format_time_col(r.get("scheduled_time"))
            r["is_active"] = bool(r.get("is_active", True))
        return rows

    async def delete_scheduled_task(self, guild_id: int, task_id: int) -> bool:
        """Delete a scheduled task by ID."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> bool:
                await cur.execute(
                    "DELETE FROM scheduled_tasks WHERE id = %s AND guild_id = %s",
                    (task_id, guild_id),
                )
                return cur.rowcount > 0

            return await self._run_mysql(_op, guild_id=guild_id)

        async with self._sqlite_connect() as conn:
            cur = await conn.execute(
                "DELETE FROM scheduled_tasks WHERE id = ? AND guild_id = ?",
                (task_id, guild_id),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def claim_due_scheduled_tasks(
        self,
        worker_id: str = config.WORKER_ID,
        now_utc: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Phase 2 Distributed Task Locking:
        Atomically claim active scheduled tasks due at the current minute
        that have not run in the last 65 seconds, locking them with `locked_by = worker_id`.
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        current_time_str = now_utc.strftime("%H:%M:00")
        next_min_str = (now_utc + timedelta(minutes=1)).strftime("%H:%M:00")
        day_abbr = now_utc.strftime("%a").lower()  # mon, tue, wed, thu, fri, sat, sun

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                # Claim tasks in this minute window that aren't already run or locked
                await cur.execute(
                    """
                    UPDATE scheduled_tasks
                    SET locked_by = %s,
                        last_run_at = CURRENT_TIMESTAMP(3)
                    WHERE is_active = TRUE
                      AND scheduled_time >= %s AND scheduled_time < %s
                      AND (last_run_at IS NULL OR last_run_at < (UTC_TIMESTAMP(3) - INTERVAL 65 SECOND))
                      AND (locked_by IS NULL OR locked_by = %s)
                    """,
                    (worker_id, current_time_str, next_min_str, worker_id),
                )
                if cur.rowcount == 0:
                    return []
                await cur.execute(
                    """
                    SELECT * FROM scheduled_tasks
                    WHERE locked_by = %s
                      AND last_run_at >= (UTC_TIMESTAMP(3) - INTERVAL 15 SECOND)
                    """,
                    (worker_id,),
                )
                return list(await cur.fetchall())

            claimed = await self._run_mysql(_op)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT * FROM scheduled_tasks
                    WHERE is_active = 1 AND substr(scheduled_time, 1, 5) = ?
                    """,
                    (current_time_str[:5],),
                )
                claimed = [dict(r) for r in await cursor.fetchall()]

        # Filter by day of week ('daily', 'weekdays', 'weekends', or comma-separated 'mon,wed,fri')
        due: list[dict] = []
        for task in claimed:
            dow = str(task.get("days_of_week") or "daily").lower()
            is_weekday = day_abbr in ("mon", "tue", "wed", "thu", "fri")
            is_weekend = day_abbr in ("sat", "sun")
            if (
                dow == "daily"
                or (dow == "weekdays" and is_weekday)
                or (dow == "weekends" and is_weekend)
                or day_abbr in [d.strip()[:3] for d in dow.split(",")]
            ):
                task["scheduled_time"] = _format_time_col(task.get("scheduled_time"))
                due.append(task)
        return due

    async def release_scheduled_task_lock(self, task_id: int) -> None:
        """Clear `locked_by` after a scheduled playback task finishes launching."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute(
                    "UPDATE scheduled_tasks SET locked_by = NULL WHERE id = %s",
                    (task_id,),
                )

            await self._run_mysql(_op)

    # ── 8. Session State Recovery (GEN 5.0 NEW: session_state) ────────────────

    async def save_session_state(
        self,
        guild_id:        int,
        channel_id:      int,
        text_channel_id: int,
        now_playing:     Optional[Track],
        elapsed_secs:    int,
        queue:           list[Track],
        loop_mode:       str,
        volume:          float,
        effects:         list[str],
    ) -> None:
        """
        Upsert full playback state for a guild, updating `saved_at` and `last_heartbeat`.
        Called on graceful shutdown AND every 60s as a heartbeat.
        """
        now_json    = json.dumps(now_playing.to_dict(), ensure_ascii=False) if now_playing else None
        queue_json  = json.dumps([t.to_dict() for t in queue], ensure_ascii=False)
        effect_json = json.dumps(effects, ensure_ascii=False)

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute(
                    """
                    INSERT INTO session_state (
                        guild_id, channel_id, text_channel_id, now_playing, elapsed_secs,
                        queue_json, loop_mode, volume, effects_json, saved_at, last_heartbeat
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)) AS new_st
                    ON DUPLICATE KEY UPDATE
                        channel_id      = new_st.channel_id,
                        text_channel_id = new_st.text_channel_id,
                        now_playing     = new_st.now_playing,
                        elapsed_secs    = new_st.elapsed_secs,
                        queue_json      = new_st.queue_json,
                        loop_mode       = new_st.loop_mode,
                        volume          = new_st.volume,
                        effects_json    = new_st.effects_json,
                        saved_at        = CURRENT_TIMESTAMP(3),
                        last_heartbeat  = CURRENT_TIMESTAMP(3)
                    """,
                    (
                        guild_id, channel_id, text_channel_id, now_json, int(elapsed_secs),
                        queue_json, str(loop_mode)[:16], float(volume), effect_json,
                    ),
                )

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO session_state
                        (guild_id, channel_id, text_channel_id, now_playing, elapsed_secs,
                         queue_json, loop_mode, volume, effects_json, saved_at, last_heartbeat)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        channel_id      = excluded.channel_id,
                        text_channel_id = excluded.text_channel_id,
                        now_playing     = excluded.now_playing,
                        elapsed_secs    = excluded.elapsed_secs,
                        queue_json      = excluded.queue_json,
                        loop_mode       = excluded.loop_mode,
                        volume          = excluded.volume,
                        effects_json    = excluded.effects_json,
                        saved_at        = CURRENT_TIMESTAMP,
                        last_heartbeat  = CURRENT_TIMESTAMP
                    """,
                    (guild_id, channel_id, text_channel_id, now_json, elapsed_secs,
                     queue_json, loop_mode, volume, effect_json),
                )
                await conn.commit()

    async def load_session_state(self, guild_id: int) -> Optional[dict]:
        """Load saved session state for a guild."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> Optional[dict]:
                await cur.execute(
                    "SELECT * FROM session_state WHERE guild_id = %s",
                    (guild_id,),
                )
                return await cur.fetchone()

            row = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM session_state WHERE guild_id = ?",
                    (guild_id,),
                )
                r = await cursor.fetchone()
                row = dict(r) if r else None

        if not row:
            return None
        try:
            now_playing = Track.from_json(row["now_playing"]) if row.get("now_playing") else None
            queue_data  = _parse_json_col(row.get("queue_json"), [])
            queue       = [Track.from_dict(d) for d in queue_data]
            effects     = _parse_json_col(row.get("effects_json"), [])
            return {
                "channel_id":      int(row["channel_id"]),
                "text_channel_id": int(row["text_channel_id"]),
                "now_playing":     now_playing,
                "elapsed_secs":    int(row["elapsed_secs"]),
                "queue":           queue,
                "loop_mode":       row["loop_mode"],
                "volume":          float(row["volume"]),
                "effects":         effects,
            }
        except Exception as exc:
            logger.warning("Session state load error guild %d: %s", guild_id, exc)
            return None

    async def clear_session_state(self, guild_id: int) -> None:
        """Remove saved session state after successful restore."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                await cur.execute("DELETE FROM session_state WHERE guild_id = %s", (guild_id,))

            await self._run_mysql(_op, guild_id=guild_id)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                await conn.execute("DELETE FROM session_state WHERE guild_id = ?", (guild_id,))
                await conn.commit()

    # ── 9. Analytics Buffer & Aggregation ─────────────────────────────────────

    async def log_event(
        self, guild_id: int, event_type: str, payload: dict | None = None
    ) -> None:
        """
        Fire-and-forget buffered analytics log.
        Flushed in batch transactions every ANALYTICS_FLUSH_INTERVAL seconds
        or when buffer reaches ANALYTICS_FLUSH_SIZE.
        """
        try:
            payload_json = json.dumps(payload or {}, ensure_ascii=False)
            async with self._analytics_lock:
                if len(self._analytics_buf) >= ANALYTICS_BUF_MAX:
                    self._analytics_buf.pop(0)
                self._analytics_buf.append((guild_id, event_type[:64], payload_json))
                should_flush = len(self._analytics_buf) >= ANALYTICS_FLUSH_SIZE

            if should_flush:
                await self._flush_analytics_buffer()
        except Exception as exc:
            logger.debug("analytics log_event error: %s", exc)

    async def _flush_analytics_buffer(self) -> None:
        """Write all buffered analytics events in a single batch transaction."""
        async with self._analytics_lock:
            if not self._analytics_buf:
                return
            batch = self._analytics_buf.copy()
            self._analytics_buf.clear()

        if not batch:
            return
        try:
            if self.is_mysql_active:
                async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                    await cur.executemany(
                        "INSERT INTO analytics (guild_id, event_type, payload) VALUES (%s, %s, %s)",
                        batch,
                    )

                await self._run_mysql(_op)

            if self._migration_mode in ("dual_write", "sqlite_fallback"):
                async with self._sqlite_connect() as conn:
                    await conn.executemany(
                        "INSERT INTO analytics (guild_id, event_type, payload) VALUES (?, ?, ?)",
                        batch,
                    )
                    await conn.commit()

            logger.debug("Analytics flush: %d events committed", len(batch))
        except Exception as exc:
            logger.warning("Analytics flush error: %s", exc)
            async with self._analytics_lock:
                self._analytics_buf[:0] = batch
                if len(self._analytics_buf) > ANALYTICS_BUF_MAX:
                    drop_count = len(self._analytics_buf) - ANALYTICS_BUF_MAX
                    del self._analytics_buf[:drop_count]
                    logger.warning("Analytics buffer exceeded %d; dropped %d oldest events", ANALYTICS_BUF_MAX, drop_count)

    async def _analytics_flush_loop(self) -> None:
        """Background loop: flush analytics buffer every ANALYTICS_FLUSH_INTERVAL seconds."""
        while True:
            try:
                await asyncio.sleep(ANALYTICS_FLUSH_INTERVAL)
                await self._flush_analytics_buffer()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Analytics flush loop error: %s", exc)

    async def get_analytics(
        self, guild_id: int, days: int = 7
    ) -> dict[str, Any]:
        """Return aggregated analytics for a guild over the last N days."""
        await self._flush_analytics_buffer()

        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> tuple[list[dict], list[dict], Optional[dict]]:
                await cur.execute(
                    """
                    SELECT ANY_VALUE(track_data) AS track_data, COUNT(*) AS plays
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    GROUP BY JSON_UNQUOTE(JSON_EXTRACT(track_data, '$.url'))
                    ORDER BY plays DESC
                    LIMIT 10
                    """,
                    (guild_id, int(days)),
                )
                top_raw = list(await cur.fetchall())

                await cur.execute(
                    """
                    SELECT DATE_FORMAT(played_at, '%%H') AS hour, COUNT(*) AS plays
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    GROUP BY hour
                    ORDER BY hour ASC
                    """,
                    (guild_id, int(days)),
                )
                hourly = list(await cur.fetchall())

                await cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    """,
                    (guild_id, int(days)),
                )
                total_row = await cur.fetchone()
                return top_raw, hourly, total_row

            top_raw, hourly, total_row = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT track_data, COUNT(*) as plays
                    FROM history
                    WHERE guild_id = ? AND played_at > datetime('now', ?)
                    GROUP BY json_extract(track_data, '$.url')
                    ORDER BY plays DESC LIMIT 10
                    """,
                    (guild_id, f"-{days} days"),
                )
                top_raw = [dict(r) for r in await cursor.fetchall()]

                cursor = await conn.execute(
                    """
                    SELECT strftime('%H', played_at) as hour, COUNT(*) as plays
                    FROM history
                    WHERE guild_id = ? AND played_at > datetime('now', ?)
                    GROUP BY hour ORDER BY hour
                    """,
                    (guild_id, f"-{days} days"),
                )
                hourly = [dict(r) for r in await cursor.fetchall()]

                cursor = await conn.execute(
                    "SELECT COUNT(*) as cnt FROM history WHERE guild_id = ? AND played_at > datetime('now', ?)",
                    (guild_id, f"-{days} days"),
                )
                tr = await cursor.fetchone()
                total_row = dict(tr) if tr else None

        top_tracks = []
        for row in top_raw:
            try:
                t = Track.from_json(row["track_data"])
                top_tracks.append({"title": t.title, "url": t.url, "plays": int(row["plays"])})
            except Exception:
                pass

        return {
            "total_plays": int(total_row["cnt"]) if total_row else 0,
            "top_tracks":  top_tracks,
            "hourly":      [{"hour": str(r["hour"]), "plays": int(r["plays"])} for r in hourly],
            "days":        days,
        }

    async def prune_analytics(self, days: int = config.ANALYTICS_RETENTION_DAYS) -> int:
        """
        Automated Retention Policy: Delete analytics older than N days (default 90 days).
        Returns rows deleted.
        """
        await self._flush_analytics_buffer()
        deleted = 0
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> int:
                await cur.execute(
                    "DELETE FROM analytics WHERE ts < (UTC_TIMESTAMP(3) - INTERVAL %s DAY)",
                    (int(days),),
                )
                return cur.rowcount

            deleted = await self._run_mysql(_op)

        if self._migration_mode in ("dual_write", "sqlite_fallback"):
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM analytics WHERE ts < datetime('now', ?)",
                    (f"-{days} days",),
                )
                await conn.commit()
                if not self.is_mysql_active:
                    deleted = cursor.rowcount
        return deleted

    # ── Tier Analytics (analytics_cog) ────────────────────────────────────────

    async def get_analytics_heatmap(
        self, guild_id: int, days: int = 30
    ) -> dict[str, list[int]]:
        """
        Return a day-of-week × hour heatmap for a guild over the last N days.
        Keyed by Mon…Sun, each value is a list of 24 integers (hours 0–23).
        """
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap: dict[str, list[int]] = {d: [0] * 24 for d in day_names}

        if self.is_mysql_active:
            # MySQL WEEKDAY() returns 0 = Monday … 6 = Sunday directly!
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT
                        WEEKDAY(played_at) AS dow,
                        HOUR(played_at)    AS hour,
                        COUNT(*)           AS plays
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    GROUP BY dow, hour
                    """,
                    (guild_id, int(days)),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
            for row in rows:
                idx = int(row["dow"])
                hour = int(row["hour"])
                if 0 <= idx < 7 and 0 <= hour < 24:
                    heatmap[day_names[idx]][hour] += int(row["plays"])
            return heatmap

        async with self._sqlite_connect() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    CAST(strftime('%w', played_at) AS INTEGER) AS dow,
                    CAST(strftime('%H', played_at) AS INTEGER) AS hour,
                    COUNT(*) AS plays
                FROM history
                WHERE guild_id = ?
                  AND played_at > datetime('now', ?)
                GROUP BY dow, hour
                """,
                (guild_id, f"-{days} days"),
            )
            rows = await cursor.fetchall()

        dow_remap = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        for row in rows:
            idx = dow_remap.get(int(row["dow"]), 0)
            hour = int(row["hour"])
            heatmap[day_names[idx]][hour] += int(row["plays"])

        return heatmap

    async def get_analytics_genre(
        self, guild_id: int, days: int = 30
    ) -> list[dict]:
        """Infer genre distribution from track metadata over the last N days."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT track_data
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    """,
                    (guild_id, int(days)),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT track_data FROM history
                    WHERE guild_id = ? AND played_at > datetime('now', ?)
                    """,
                    (guild_id, f"-{days} days"),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        _GENRE_RULES: list[tuple[str, list[str]]] = [
            ("Lo-fi",     ["lofi", "lo-fi", "lo fi", "chillhop", "chill hop"]),
            ("Hip-Hop",   ["hip hop", "hiphop", "rap", "trap", "drill", "freestyle"]),
            ("Pop",       ["pop", "idol", "k-pop", "kpop", "j-pop", "jpop"]),
            ("Rock",      ["rock", "metal", "punk", "grunge", "hardcore", "alt rock"]),
            ("EDM",       ["edm", "techno", "house", "trance", "dubstep", "dnb",
                           "drum and bass", "electro", "rave", "progressive"]),
            ("Jazz",      ["jazz", "blues", "soul", "swing", "bebop"]),
            ("Classical", ["classical", "orchestra", "symphony", "piano", "violin",
                           "beethoven", "mozart", "bach"]),
            ("R&B",       ["r&b", "rnb", "rhythm", "funk", "neo soul"]),
            ("Country",   ["country", "bluegrass", "folk", "americana"]),
            ("Anime",     ["anime", "ost", "opening", "ending", "insert song",
                           "jp", "vocaloid", "touhou"]),
            ("Gaming",    ["game", "gaming", "ost", "soundtrack", "8bit", "chiptune"]),
        ]

        counts: dict[str, int] = {}
        for row in rows:
            try:
                t = Track.from_json(row["track_data"])
                haystack = (t.title + " " + t.uploader).lower()
            except Exception:
                continue

            matched = False
            for genre, keywords in _GENRE_RULES:
                if any(kw in haystack for kw in keywords):
                    counts[genre] = counts.get(genre, 0) + 1
                    matched = True
                    break
            if not matched:
                counts["Other"] = counts.get("Other", 0) + 1

        return sorted(
            [{"genre": g, "count": c} for g, c in counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

    async def get_analytics_peak_hours(
        self, guild_id: int, days: int = 30
    ) -> list[dict]:
        """Return hourly play counts sorted descending (top peak hours)."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT
                        HOUR(played_at) AS hour,
                        COUNT(*)        AS plays
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    GROUP BY hour
                    ORDER BY plays DESC
                    """,
                    (guild_id, int(days)),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT
                        CAST(strftime('%H', played_at) AS INTEGER) AS hour,
                        COUNT(*) AS plays
                    FROM history
                    WHERE guild_id = ?
                      AND played_at > datetime('now', ?)
                    GROUP BY hour
                    ORDER BY plays DESC
                    """,
                    (guild_id, f"-{days} days"),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        return [{"hour": int(r["hour"]), "plays": int(r["plays"])} for r in rows]

    async def get_analytics_top_entities(
        self, guild_id: int, days: int = 30, limit: int = 10
    ) -> dict:
        """Return top artists (uploaders) and top channels from history metadata."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                await cur.execute(
                    """
                    SELECT track_data, duration_played
                    FROM history
                    WHERE guild_id = %s
                      AND played_at > (UTC_TIMESTAMP(3) - INTERVAL %s DAY)
                    """,
                    (guild_id, int(days)),
                )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT track_data, duration_played
                    FROM history
                    WHERE guild_id = ?
                      AND played_at > datetime('now', ?)
                    """,
                    (guild_id, f"-{days} days"),
                )
                rows = [dict(r) for r in await cursor.fetchall()]

        artist_plays:   dict[str, int] = {}
        artist_minutes: dict[str, int] = {}
        channel_plays:  dict[str, int] = {}

        for row in rows:
            try:
                t = Track.from_json(row["track_data"])
            except Exception:
                continue
            uploader = t.uploader or "Unknown"
            artist_plays[uploader]   = artist_plays.get(uploader, 0) + 1
            artist_minutes[uploader] = (
                artist_minutes.get(uploader, 0)
                + int(row["duration_played"] or 0) // 60
            )
            channel_plays[uploader] = channel_plays.get(uploader, 0) + 1

        artists = sorted(
            [
                {
                    "name":    name,
                    "plays":   plays,
                    "minutes": artist_minutes.get(name, 0),
                }
                for name, plays in artist_plays.items()
            ],
            key=lambda x: x["plays"],
            reverse=True,
        )[:limit]

        channels = sorted(
            [{"name": name, "plays": plays} for name, plays in channel_plays.items()],
            key=lambda x: x["plays"],
            reverse=True,
        )[:limit]

        return {"artists": artists, "channels": channels}

    async def get_analytics_streak(
        self, guild_id: int, user_id: int | None = None
    ) -> dict:
        """Compute listening streak (consecutive active days)."""
        if self.is_mysql_active:
            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> list[dict]:
                if user_id:
                    await cur.execute(
                        """
                        SELECT DISTINCT DATE_FORMAT(played_at, '%%Y-%%m-%%d') AS day
                        FROM history
                        WHERE guild_id = %s AND user_id = %s
                        ORDER BY day DESC
                        """,
                        (guild_id, user_id),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT DISTINCT DATE_FORMAT(played_at, '%%Y-%%m-%%d') AS day
                        FROM history
                        WHERE guild_id = %s
                        ORDER BY day DESC
                        """,
                        (guild_id,),
                    )
                return list(await cur.fetchall())

            rows = await self._run_mysql(_op, guild_id=guild_id, readonly=True)
        else:
            async with self._sqlite_connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        """
                        SELECT DISTINCT date(played_at) AS day
                        FROM history WHERE guild_id = ? AND user_id = ?
                        ORDER BY day DESC
                        """,
                        (guild_id, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT DISTINCT date(played_at) AS day
                        FROM history WHERE guild_id = ?
                        ORDER BY day DESC
                        """,
                        (guild_id,),
                    )
                rows = [dict(r) for r in await cursor.fetchall()]

        active_dates: list[date] = []
        for row in rows:
            try:
                d_val = row["day"]
                if isinstance(d_val, date):
                    active_dates.append(d_val)
                else:
                    active_dates.append(date.fromisoformat(str(d_val)))
            except Exception:
                pass

        if not active_dates:
            return {
                "current_streak": 0,
                "longest_streak": 0,
                "total_days":     0,
                "active_dates":   [],
            }

        today = date.today()
        current = 0
        check = today
        active_set = set(active_dates)
        while check in active_set:
            current += 1
            check -= timedelta(days=1)

        if current == 0:
            check = today - timedelta(days=1)
            while check in active_set:
                current += 1
                check -= timedelta(days=1)

        longest = 1
        run     = 1
        sorted_dates = sorted(active_dates)
        for i in range(1, len(sorted_dates)):
            if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
                run += 1
                longest = max(longest, run)
            else:
                run = 1

        return {
            "current_streak": current,
            "longest_streak": longest,
            "total_days":     len(active_dates),
            "active_dates":   [d.isoformat() for d in active_dates[:30]],
        }

    # ── Zero-Downtime Reconciliation & Verification ───────────────────────────

    async def verify_migration_checksums(self) -> dict[str, Any]:
        """
        Section 4 Step 3 (Reconciliation & Verification):
        Compare row counts and SHA-256 key checksums between SQLite and MySQL.
        """
        if not self.is_mysql_active:
            return {"ok": False, "error": "MySQL pool is not active"}
        if not Path(self._db_path).exists() and self._db_path != ":memory:":
            return {"ok": False, "error": f"SQLite file {self._db_path} not found"}

        tables_to_check = [
            ("server_configs", "guild_id"),
            ("queue",          "guild_id, position"),
            ("history",        "guild_id, user_id"),
            ("user_stats",     "user_id, guild_id"),
            ("favorites",      "user_id, guild_id, name"),
            ("session_state",  "guild_id"),
            ("analytics",      "guild_id, event_type"),
        ]
        report: dict[str, Any] = {"tables": {}, "all_matched": True}

        async with self._sqlite_connect() as sconn:
            for tbl, key_cols in tables_to_check:
                try:
                    scur = await sconn.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}")
                    srow = await scur.fetchone()
                    sqlite_cnt = int(srow["cnt"]) if srow else 0

                    scur2 = await sconn.execute(f"SELECT {key_cols} FROM {tbl} ORDER BY {key_cols}")
                    s_keys = await scur2.fetchall()
                    s_hash = hashlib.sha256(
                        "|".join(",".join(str(v) for v in r) for r in s_keys).encode("utf-8")
                    ).hexdigest()[:16]
                except Exception:
                    sqlite_cnt = 0
                    s_hash = "none"

                async def _mysql_check(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> tuple[int, str]:
                    await cur.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}")
                    mrow = await cur.fetchone()
                    mcnt = int(mrow["cnt"]) if mrow else 0
                    await cur.execute(f"SELECT {key_cols} FROM {tbl} ORDER BY {key_cols}")
                    m_keys = await cur.fetchall()
                    cols = [c.strip() for c in key_cols.split(",")]
                    m_hash = hashlib.sha256(
                        "|".join(",".join(str(r[c]) for c in cols) for r in m_keys).encode("utf-8")
                    ).hexdigest()[:16]
                    return mcnt, m_hash

                mysql_cnt, m_hash = await self._run_mysql(_mysql_check, readonly=True)
                matched = (sqlite_cnt == mysql_cnt) and (s_hash == m_hash)
                if not matched:
                    report["all_matched"] = False
                report["tables"][tbl] = {
                    "sqlite_count":    sqlite_cnt,
                    "mysql_count":     mysql_cnt,
                    "sqlite_checksum": s_hash,
                    "mysql_checksum":  m_hash,
                    "matched":         matched,
                }

        return report
