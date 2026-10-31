# -*- coding: utf-8 -*-
"""
scripts/migrate_sqlite_to_mysql.py — Enterprise SQLite ➔ MySQL 8.0+ Migration & Reconciliation Engine.

Implements Section 4 of the Database Transition Strategy:
  1. Schema Initialization: Connects to MySQL 8.0+ and executes the normalized DDL + partitions.
  2. Cursor Row Streaming: Streams from SQLite in chunks of 1,000 rows (`fetchmany(1000)`).
  3. Data Type Normalization:
     - Unpacks `server_configs.config_data` JSON into 20 normalized MySQL columns.
     - Normalizes JSON strings into MySQL Native JSON.
     - Converts ISO-8601 / SQLite timestamps into MySQL `DATETIME(3)` (`YYYY-MM-DD HH:MM:SS.fff`).
     - Normalizes `queue_bookmarks` into `user_playlists` + `playlist_tracks`.
  4. Bulk Insert: Uses `executemany` with batch size 500–1,000 rows per transaction.
  5. Reconciliation & Verification: Compares row counts and SHA-256 checksums between SQLite and MySQL.

Usage:
  python -m scripts.migrate_sqlite_to_mysql --mode offline --batch-size 1000
  python -m scripts.migrate_sqlite_to_mysql --mode zero-downtime --batch-size 500
  python -m scripts.migrate_sqlite_to_mysql --verify-only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

# Ensure project root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import aiosqlite
import aiomysql

import config
from core.database import DatabaseManager, MYSQL_SCHEMA_DDL
from models.server_config import ServerConfig

logger = logging.getLogger("migrate_sqlite_to_mysql")


# ── Data Type Normalization Helpers ───────────────────────────────────────────

def normalize_datetime3(val: Any) -> str:
    """
    Convert an ISO-8601 string, SQLite timestamp string, unix float, or datetime
    into MySQL `DATETIME(3)` format: `'YYYY-MM-DD HH:MM:SS.mmm'`.
    """
    if val is None or val == "":
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    if isinstance(val, datetime):
        if val.tzinfo is not None:
            val = val.astimezone(timezone.utc).replace(tzinfo=None)
        return val.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    if isinstance(val, (int, float)):
        dt_obj = datetime.fromtimestamp(float(val), tz=timezone.utc).replace(tzinfo=None)
        return dt_obj.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    s = str(val).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt_obj = datetime.fromisoformat(s)
        if dt_obj.tzinfo is not None:
            dt_obj = dt_obj.astimezone(timezone.utc).replace(tzinfo=None)
        return dt_obj.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt_obj = datetime.strptime(s, fmt)
            return dt_obj.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except ValueError:
            continue

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def normalize_json(val: Any, fallback: Any = None) -> Optional[str]:
    """
    Validate and normalize a JSON string / dict / list into a clean UTF-8 JSON string
    suitable for a MySQL Native `JSON` column.
    """
    if val is None:
        return json.dumps(fallback, ensure_ascii=False) if fallback is not None else None
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    s = str(val).strip()
    if not s:
        return json.dumps(fallback, ensure_ascii=False) if fallback is not None else None
    try:
        parsed = json.loads(s)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        return json.dumps(fallback if fallback is not None else {}, ensure_ascii=False)


def normalize_server_config_row(sqlite_row: dict[str, Any]) -> tuple:
    """
    Convert a SQLite `server_configs` row (`guild_id, config_data, created_at, updated_at`)
    into the 22-element tuple required by the normalized MySQL `server_configs` table.
    """
    guild_id = int(sqlite_row["guild_id"])
    raw_json = sqlite_row.get("config_data") or "{}"
    try:
        data = json.loads(raw_json) if isinstance(raw_json, str) else dict(raw_json)
        data["guild_id"] = guild_id
        cfg = ServerConfig.from_dict(data)
    except Exception:
        cfg = ServerConfig.default(guild_id)

    base_params = cfg.to_db_params()  # 20 items
    created_at = normalize_datetime3(sqlite_row.get("created_at"))
    updated_at = normalize_datetime3(sqlite_row.get("updated_at"))
    return (*base_params, created_at, updated_at)


# ── Cursor Streaming Helper ───────────────────────────────────────────────────

async def stream_sqlite_table(
    sqlite_conn: aiosqlite.Connection,
    query: str,
    chunk_size: int = 1000,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Stream rows from SQLite `chunk_size` (default 1,000) rows at a time."""
    cursor = await sqlite_conn.execute(query)
    while True:
        rows = await cursor.fetchmany(chunk_size)
        if not rows:
            break
        yield [dict(r) for r in rows]


async def _sqlite_table_exists(sqlite_conn: aiosqlite.Connection, table_name: str) -> bool:
    cur = await sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    row = await cur.fetchone()
    return row is not None


# ── Migration Engine ──────────────────────────────────────────────────────────

class SQLiteToMySQLMigrator:
    """
    Full-featured migrator supporting Offline & Zero-Downtime Snapshot migration,
    1,000-row cursor streaming, data normalization, 500–1,000 row batch inserts,
    and SHA-256 Checksum verification.
    """

    def __init__(
        self,
        sqlite_path: str = config.DATABASE_PATH,
        db_manager: Optional[DatabaseManager] = None,
        stream_size: int = 1000,
        batch_size: int = 1000,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.db_manager = db_manager
        self.stream_size = max(100, min(stream_size, 5000))
        self.batch_size = max(100, min(batch_size, 2000))

    async def migrate_all(self, mode: str = "offline") -> dict[str, Any]:
        """Run full schema init, row streaming migration, and checksum reconciliation."""
        t0 = time.monotonic()
        own_db = False
        if self.db_manager is None:
            self.db_manager = DatabaseManager(
                self.sqlite_path,
                migration_mode="mysql" if mode == "offline" else "dual_write",
            )
            await self.db_manager.initialise()
            own_db = True

        summary: dict[str, Any] = {
            "mode": mode,
            "tables_migrated": {},
            "verification": {},
            "elapsed_seconds": 0.0,
        }

        try:
            if not Path(self.sqlite_path).exists():
                logger.warning("SQLite database %s does not exist — nothing to migrate.", self.sqlite_path)
                return summary

            async with aiosqlite.connect(self.sqlite_path) as sconn:
                sconn.row_factory = aiosqlite.Row

                summary["tables_migrated"]["server_configs"] = await self._migrate_server_configs(sconn)
                summary["tables_migrated"]["queue"]          = await self._migrate_queue(sconn)
                summary["tables_migrated"]["history"]        = await self._migrate_history(sconn)
                summary["tables_migrated"]["user_stats"]     = await self._migrate_user_stats(sconn)
                summary["tables_migrated"]["favorites"]      = await self._migrate_favorites(sconn)
                summary["tables_migrated"]["playlists"]      = await self._migrate_bookmarks_and_playlists(sconn)
                summary["tables_migrated"]["session_state"]  = await self._migrate_session_state(sconn)
                summary["tables_migrated"]["analytics"]      = await self._migrate_analytics(sconn)
                summary["tables_migrated"]["search_history"] = await self._migrate_search_history(sconn)

            # Step 3: Reconciliation & Checksum Verification
            summary["verification"] = await self.db_manager.verify_migration_checksums()
            summary["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            return summary

        finally:
            if own_db and self.db_manager:
                await self.db_manager.close()

    async def _batch_execute(self, sql: str, rows: list[tuple]) -> int:
        """Execute bulk insert in batches of `self.batch_size` (500–1,000 rows)."""
        assert self.db_manager is not None
        total = 0
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]

            async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> int:
                await cur.executemany(sql, batch)
                return len(batch)

            total += await self.db_manager._run_mysql(_op)
        return total

    async def _migrate_server_configs(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "server_configs"):
            return 0
        sql = """
            INSERT INTO server_configs (
                guild_id, volume, audio_quality, max_track_len, idle_timeout,
                dj_only, dj_role_id, request_channel_id, timezone,
                auto_playlist, auto_playlist_size, smart_autoplay,
                queue_locked, queue_add_permission, duplicate_mode,
                auto_leave_alone, auto_pause_empty, embed_theme,
                language, guild_presets, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
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
                updated_at           = new_cfg.updated_at
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM server_configs", self.stream_size):
            batch = [normalize_server_config_row(r) for r in chunk]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_queue(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "queue"):
            return 0
        sql = """
            INSERT INTO queue (guild_id, channel_id, track_data, position, user_id, added_at)
            VALUES (%s, %s, %s, %s, %s, %s) AS new_q
            ON DUPLICATE KEY UPDATE
                channel_id = new_q.channel_id,
                track_data = new_q.track_data,
                user_id    = new_q.user_id,
                added_at   = new_q.added_at
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM queue ORDER BY guild_id, position", self.stream_size):
            batch = [
                (
                    int(r["guild_id"]),
                    int(r["channel_id"]),
                    normalize_json(r["track_data"], {}),
                    int(r["position"]),
                    int(r.get("user_id") or 0),
                    normalize_datetime3(r.get("added_at")),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_history(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "history"):
            return 0
        sql = """
            INSERT IGNORE INTO history (
                id, guild_id, user_id, track_data, played_at,
                duration_played, skipped, completed
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM history ORDER BY id", self.stream_size):
            batch = [
                (
                    int(r["id"]),
                    int(r["guild_id"]),
                    int(r["user_id"]),
                    normalize_json(r["track_data"], {}),
                    normalize_datetime3(r.get("played_at")),
                    max(0, int(r.get("duration_played") or 0)),
                    bool(r.get("skipped", False)),
                    bool(r.get("completed", False)),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_user_stats(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "user_stats"):
            return 0
        sql = """
            INSERT INTO user_stats (
                user_id, guild_id, total_tracks_requested,
                total_listening_time, favorite_tracks, last_active
            ) VALUES (%s, %s, %s, %s, %s, %s) AS new_us
            ON DUPLICATE KEY UPDATE
                total_tracks_requested = GREATEST(user_stats.total_tracks_requested, new_us.total_tracks_requested),
                total_listening_time   = GREATEST(user_stats.total_listening_time, new_us.total_listening_time),
                favorite_tracks        = new_us.favorite_tracks,
                last_active            = new_us.last_active
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM user_stats", self.stream_size):
            batch = [
                (
                    int(r["user_id"]),
                    int(r["guild_id"]),
                    max(0, int(r.get("total_tracks_requested") or 0)),
                    max(0, int(r.get("total_listening_time") or 0)),
                    normalize_json(r.get("favorite_tracks"), []),
                    normalize_datetime3(r.get("last_active")),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_favorites(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "favorites"):
            return 0
        sql = """
            INSERT INTO favorites (user_id, guild_id, name, track_data, created_at)
            VALUES (%s, %s, %s, %s, %s) AS new_fav
            ON DUPLICATE KEY UPDATE
                track_data = new_fav.track_data
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM favorites", self.stream_size):
            batch = [
                (
                    int(r["user_id"]),
                    int(r["guild_id"]),
                    str(r["name"])[:255],
                    normalize_json(r["track_data"], {}),
                    normalize_datetime3(r.get("created_at")),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_bookmarks_and_playlists(self, sconn: aiosqlite.Connection) -> int:
        """Migrate legacy `queue_bookmarks` into Gen 5.0 `user_playlists` + `playlist_tracks`."""
        assert self.db_manager is not None
        if not await _sqlite_table_exists(sconn, "queue_bookmarks"):
            return 0

        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM queue_bookmarks", self.stream_size):
            for r in chunk:
                user_id = int(r["user_id"])
                guild_id = int(r["guild_id"])
                name = str(r["name"]).strip()[:100]
                created_at = normalize_datetime3(r.get("created_at"))
                try:
                    tracks_list = json.loads(r.get("tracks_json") or "[]")
                except Exception:
                    tracks_list = []

                async def _op(conn: aiomysql.Connection, cur: aiomysql.DictCursor) -> None:
                    await cur.execute(
                        """
                        INSERT INTO user_playlists (
                            user_id, guild_id, name, description, scope, is_public, created_at
                        ) VALUES (%s, %s, %s, %s, 'personal', FALSE, %s) AS new_pl
                        ON DUPLICATE KEY UPDATE
                            updated_at = CURRENT_TIMESTAMP(3)
                        """,
                        (user_id, guild_id, name, "Migrated from V3 Queue Bookmark", created_at),
                    )
                    await cur.execute(
                        "SELECT id FROM user_playlists WHERE user_id = %s AND name = %s",
                        (user_id, name),
                    )
                    pl_row = await cur.fetchone()
                    if pl_row and tracks_list:
                        pl_id = pl_row["id"]
                        await cur.execute("DELETE FROM playlist_tracks WHERE playlist_id = %s", (pl_id,))
                        track_rows = [
                            (pl_id, pos, normalize_json(t_dict, {}), created_at)
                            for pos, t_dict in enumerate(tracks_list)
                        ]
                        await cur.executemany(
                            """
                            INSERT INTO playlist_tracks (playlist_id, position, track_data, added_at)
                            VALUES (%s, %s, %s, %s)
                            """,
                            track_rows,
                        )

                await self.db_manager._run_mysql(_op, guild_id=guild_id)
                count += 1
        return count

    async def _migrate_session_state(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "session_state"):
            return 0
        sql = """
            INSERT INTO session_state (
                guild_id, channel_id, text_channel_id, now_playing, elapsed_secs,
                queue_json, loop_mode, volume, effects_json, saved_at, last_heartbeat
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new_st
            ON DUPLICATE KEY UPDATE
                channel_id      = new_st.channel_id,
                text_channel_id = new_st.text_channel_id,
                now_playing     = new_st.now_playing,
                elapsed_secs    = new_st.elapsed_secs,
                queue_json      = new_st.queue_json,
                loop_mode       = new_st.loop_mode,
                volume          = new_st.volume,
                effects_json    = new_st.effects_json,
                saved_at        = new_st.saved_at,
                last_heartbeat  = new_st.last_heartbeat
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM session_state", self.stream_size):
            batch = []
            for r in chunk:
                saved = normalize_datetime3(r.get("saved_at"))
                batch.append((
                    int(r["guild_id"]),
                    int(r.get("channel_id") or 0),
                    int(r.get("text_channel_id") or 0),
                    normalize_json(r.get("now_playing"), None),
                    max(0, int(r.get("elapsed_secs") or 0)),
                    normalize_json(r.get("queue_json"), []),
                    str(r.get("loop_mode") or "off")[:16],
                    float(r.get("volume") or 1.0),
                    normalize_json(r.get("effects_json"), []),
                    saved,
                    saved,
                ))
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_analytics(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "analytics"):
            return 0
        sql = """
            INSERT IGNORE INTO analytics (id, guild_id, event_type, payload, ts)
            VALUES (%s, %s, %s, %s, %s)
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM analytics ORDER BY id", self.stream_size):
            batch = [
                (
                    int(r["id"]),
                    int(r["guild_id"]),
                    str(r.get("event_type") or "unknown")[:64],
                    normalize_json(r.get("payload"), {}),
                    normalize_datetime3(r.get("ts")),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count

    async def _migrate_search_history(self, sconn: aiosqlite.Connection) -> int:
        if not await _sqlite_table_exists(sconn, "search_history"):
            return 0
        sql = """
            INSERT IGNORE INTO search_history (id, guild_id, user_id, query, used_at)
            VALUES (%s, %s, %s, %s, %s)
        """
        count = 0
        async for chunk in stream_sqlite_table(sconn, "SELECT * FROM search_history ORDER BY id", self.stream_size):
            batch = [
                (
                    int(r["id"]),
                    int(r["guild_id"]),
                    int(r["user_id"]),
                    str(r.get("query") or "")[:200],
                    normalize_datetime3(r.get("used_at")),
                )
                for r in chunk
            ]
            count += await self._batch_execute(sql, batch)
        return count


async def _cli_main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Music Bot V3 SQLite database to MySQL 8.0+ Enterprise Schema"
    )
    parser.add_argument(
        "--sqlite-path",
        default=config.DATABASE_PATH,
        help="Path to source SQLite database file (default: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "zero-downtime"],
        default="offline",
        help="Migration mode: offline (direct cutover) or zero-downtime (dual-write snapshot)",
    )
    parser.add_argument(
        "--stream-size",
        type=int,
        default=1000,
        help="Rows fetched per cursor stream chunk from SQLite (default: 1000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows inserted per executemany batch into MySQL (default: 1000)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run row-count and SHA-256 checksum reconciliation without migrating",
    )
    args = parser.parse_args()

    migrator = SQLiteToMySQLMigrator(
        sqlite_path=args.sqlite_path,
        stream_size=args.stream_size,
        batch_size=args.batch_size,
    )

    if args.verify_only:
        db = DatabaseManager(args.sqlite_path, migration_mode="dual_write")
        await db.initialise()
        try:
            report = await db.verify_migration_checksums()
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report.get("all_matched") else 1
        finally:
            await db.close()

    result = await migrator.migrate_all(mode=args.mode)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    verified = result.get("verification", {}).get("all_matched", True)
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli_main()))

