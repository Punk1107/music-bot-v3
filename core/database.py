# -*- coding: utf-8 -*-
"""
core/database.py — Async SQLite database manager for Music Bot V3.

V3 Changes:
  - Added `favorites` table (user_id, guild_id, track_data, name, created_at)
  - Added `dj_role_id`, `request_channel_id`, `auto_playlist` to server_configs
    (handled transparently via JSON column, no migration needed)
  - Immediate queue save on enqueue (write-ahead) — no more 5-min-only saves
  - Periodic cache pruning exposed as prune_analytics(days)
  - All public methods fully typed
  - Connection pool comment: aiosqlite is single-connection; we serialize via
    asyncio.Lock. For multi-process deployments, use PostgreSQL instead.

Schema: WAL mode + NORMAL sync + FK enforcement.
Handles: queue persistence, play history, server config, user stats, search
         history, analytics, favorites.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import aiosqlite

import config
from models.track import Track
from models.server_config import ServerConfig

logger = logging.getLogger(__name__)

# ─────────────────────────── Schema SQL ──────────────────────────────────────

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous   = NORMAL;
PRAGMA foreign_keys  = ON;
PRAGMA cache_size    = 10000;
PRAGMA temp_store    = MEMORY;
PRAGMA mmap_size     = 268435456;

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

-- V3 NEW: favorites
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    guild_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    track_data TEXT    NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, guild_id, name)
);

-- Tier-S NEW: queue_bookmarks (Feature 5)
CREATE TABLE IF NOT EXISTS queue_bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    tracks_json TEXT    NOT NULL,  -- JSON array of serialised Track objects
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, guild_id, name)
);

-- Tier-S+ NEW: session_state (Feature 11 — Resume Position)
-- Stores full playback state on crash/graceful shutdown for restoration.
CREATE TABLE IF NOT EXISTS session_state (
    guild_id        INTEGER PRIMARY KEY,
    channel_id      INTEGER NOT NULL DEFAULT 0,  -- voice channel to rejoin
    text_channel_id INTEGER NOT NULL DEFAULT 0,  -- text channel for messages
    now_playing     TEXT,                         -- JSON Track or NULL
    elapsed_secs    INTEGER NOT NULL DEFAULT 0,   -- seconds into current track
    queue_json      TEXT    NOT NULL DEFAULT '[]',-- JSON array of queued Tracks
    loop_mode       TEXT    NOT NULL DEFAULT 'off',
    volume          REAL    NOT NULL DEFAULT 1.0,
    effects_json    TEXT    NOT NULL DEFAULT '[]',-- JSON list of AudioEffect values
    saved_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_queue_guild_pos       ON queue(guild_id, position);
CREATE INDEX IF NOT EXISTS idx_history_guild_user    ON history(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_history_played_at     ON history(played_at);
CREATE INDEX IF NOT EXISTS idx_history_guild_recent  ON history(guild_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_stats_guild      ON user_stats(guild_id);
CREATE INDEX IF NOT EXISTS idx_search_history_guild  ON search_history(guild_id, used_at);
CREATE INDEX IF NOT EXISTS idx_analytics_guild_ts    ON analytics(guild_id, ts);
CREATE INDEX IF NOT EXISTS idx_analytics_event       ON analytics(event_type);
CREATE INDEX IF NOT EXISTS idx_favorites_user_guild  ON favorites(user_id, guild_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user_guild  ON queue_bookmarks(user_id, guild_id);
"""


class DatabaseManager:
    """
    Async SQLite manager — single persistent connection, all writes serialised
    via asyncio.Lock for safety.

    Lifecycle:
        db = DatabaseManager()
        await db.initialise()   # called once in bot.setup_hook()
        ...
        await db.close()        # called in bot.close()
    """

    def __init__(self, db_path: str = config.DATABASE_PATH) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Open the shared connection and apply the schema."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()
        logger.info("Database initialised at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.commit()
                await self._conn.close()
            except Exception as exc:
                logger.warning("DB close error: %s", exc)
            finally:
                self._conn = None

    # ── Internal context manager ───────────────────────────────────────────────

    @asynccontextmanager
    async def _connect(self):
        """
        Yield the shared connection under write lock.
        Falls back to a temporary connection if not yet initialised.
        """
        if self._conn:
            async with self._lock:
                yield self._conn
        else:
            # Fallback: open temp connection (should not happen in normal flow)
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            try:
                yield conn
            finally:
                await conn.close()

    # ── Queue ─────────────────────────────────────────────────────────────────

    async def save_queue(
        self,
        guild_id: int,
        channel_id: int,
        tracks: list[Track],
    ) -> None:
        """Persist the full queue for a guild (replaces existing)."""
        async with self._connect() as conn:
            await conn.execute(
                "DELETE FROM queue WHERE guild_id = ?", (guild_id,)
            )
            for pos, track in enumerate(tracks):
                await conn.execute(
                    """
                    INSERT INTO queue (guild_id, channel_id, track_data, position, user_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        channel_id,
                        track.to_json(),
                        pos,
                        track.requested_by_id or 0,
                    ),
                )
            await conn.commit()

    async def load_queue(self, guild_id: int) -> list[Track]:
        """Load persisted queue for a guild, ordered by position."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT track_data FROM queue WHERE guild_id = ? ORDER BY position",
                (guild_id,),
            )
            rows = await cursor.fetchall()
        return [Track.from_json(row["track_data"]) for row in rows]

    async def clear_queue(self, guild_id: int) -> None:
        async with self._connect() as conn:
            await conn.execute("DELETE FROM queue WHERE guild_id = ?", (guild_id,))
            await conn.commit()

    # ── History ───────────────────────────────────────────────────────────────

    async def record_track_played(
        self,
        guild_id:        int,
        user_id:         int,
        track:           Track,
        duration_played: int  = 0,
        skipped:         bool = False,
        completed:       bool = False,
    ) -> None:
        """Log a track play and update user stats atomically."""
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT INTO history (guild_id, user_id, track_data, duration_played, skipped, completed)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, track.to_json(), duration_played, skipped, completed),
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
        if user_id:
            sql = """
                SELECT track_data, played_at, skipped, completed, user_id
                FROM history
                WHERE guild_id = ? AND user_id = ?
                ORDER BY played_at DESC LIMIT ?
            """
            params = (guild_id, user_id, limit)
        else:
            sql = """
                SELECT track_data, played_at, skipped, completed, user_id
                FROM history WHERE guild_id = ?
                ORDER BY played_at DESC LIMIT ?
            """
            params = (guild_id, limit)

        async with self._connect() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_tracks_for_autoplaylist(
        self, guild_id: int, limit: int = 20
    ) -> list[Track]:
        """Return distinct recently-played tracks for auto-playlist seeding."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                """
                SELECT track_data FROM history
                WHERE guild_id = ? AND skipped = FALSE AND completed = TRUE
                ORDER BY played_at DESC LIMIT ?
                """,
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
        seen_urls: set[str] = set()
        tracks: list[Track] = []
        for row in rows:
            t = Track.from_json(row["track_data"])
            if t.url not in seen_urls:
                seen_urls.add(t.url)
                tracks.append(t)
        return tracks

    # ── User Stats ────────────────────────────────────────────────────────────

    async def get_user_stats(
        self, guild_id: int, user_id: int
    ) -> Optional[dict]:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM user_stats WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_top_users(self, guild_id: int, limit: int = 5) -> list[dict]:
        async with self._connect() as conn:
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

    # ── Server Config ─────────────────────────────────────────────────────────

    async def get_server_config(self, guild_id: int) -> ServerConfig:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT config_data FROM server_configs WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
        if row:
            return ServerConfig.from_json(row["config_data"])
        return ServerConfig.default(guild_id)

    async def save_server_config(self, cfg: ServerConfig) -> None:
        async with self._connect() as conn:
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

    # ── Search History (autocomplete) ─────────────────────────────────────────

    async def add_search_history(
        self, guild_id: int, user_id: int, query: str
    ) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT INTO search_history (guild_id, user_id, query)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, query.strip()[:200]),
            )
            # Keep only latest 500 per guild
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
        async with self._connect() as conn:
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

    # ── Analytics ─────────────────────────────────────────────────────────────

    async def log_event(
        self, guild_id: int, event_type: str, payload: dict | None = None
    ) -> None:
        """Fire-and-forget analytics log. Silently swallows errors."""
        try:
            async with self._connect() as conn:
                await conn.execute(
                    "INSERT INTO analytics (guild_id, event_type, payload) VALUES (?, ?, ?)",
                    (guild_id, event_type, json.dumps(payload or {}, ensure_ascii=False)),
                )
                await conn.commit()
        except Exception as exc:
            logger.debug("analytics log_event error: %s", exc)

    async def get_analytics(
        self, guild_id: int, days: int = 7
    ) -> dict[str, Any]:
        """Return aggregated analytics for a guild over the last N days."""
        async with self._connect() as conn:
            # Top tracks from history
            cursor = await conn.execute(
                """
                SELECT track_data, COUNT(*) as plays
                FROM history
                WHERE guild_id = ?
                  AND played_at > datetime('now', ?)
                GROUP BY json_extract(track_data, '$.url')
                ORDER BY plays DESC
                LIMIT 10
                """,
                (guild_id, f"-{days} days"),
            )
            top_raw = await cursor.fetchall()

            # Hourly breakdown
            cursor = await conn.execute(
                """
                SELECT strftime('%H', played_at) as hour, COUNT(*) as plays
                FROM history
                WHERE guild_id = ?
                  AND played_at > datetime('now', ?)
                GROUP BY hour ORDER BY hour
                """,
                (guild_id, f"-{days} days"),
            )
            hourly = await cursor.fetchall()

            # Total plays
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM history WHERE guild_id = ? AND played_at > datetime('now', ?)",
                (guild_id, f"-{days} days"),
            )
            total_row = await cursor.fetchone()

        top_tracks = []
        for row in top_raw:
            try:
                t = Track.from_json(row["track_data"])
                top_tracks.append({"title": t.title, "url": t.url, "plays": row["plays"]})
            except Exception:
                pass

        return {
            "total_plays": total_row["cnt"] if total_row else 0,
            "top_tracks":  top_tracks,
            "hourly":      [{"hour": r["hour"], "plays": r["plays"]} for r in hourly],
            "days":        days,
        }

    async def prune_analytics(self, days: int = 30) -> int:
        """Delete analytics older than N days. Returns rows deleted."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM analytics WHERE ts < datetime('now', ?)",
                (f"-{days} days",),
            )
            await conn.commit()
            return cursor.rowcount

    # ── Favorites (V3 NEW) ────────────────────────────────────────────────────

    async def add_favorite(
        self, user_id: int, guild_id: int, name: str, track: Track
    ) -> bool:
        """
        Add a track to user's favorites. Returns True on success, False if
        name already exists or limit exceeded.
        """
        # Check limit
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] >= config.MAX_FAVORITES_PER_USER:
                return False

            try:
                await conn.execute(
                    """
                    INSERT INTO favorites (user_id, guild_id, name, track_data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, guild_id, name.strip()[:100], track.to_json()),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False  # UNIQUE constraint — name already exists

    async def remove_favorite(
        self, user_id: int, guild_id: int, name: str
    ) -> bool:
        """Remove a favorite by name. Returns True if it existed."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def get_favorites(
        self, user_id: int, guild_id: int
    ) -> list[dict]:
        """Return all favorites for a user in a guild."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                """
                SELECT name, track_data, created_at
                FROM favorites WHERE user_id = ? AND guild_id = ?
                ORDER BY created_at DESC
                """,
                (user_id, guild_id),
            )
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            try:
                track = Track.from_json(row["track_data"])
                result.append({
                    "name":       row["name"],
                    "track":      track,
                    "created_at": row["created_at"],
                })
            except Exception:
                pass
        return result

    async def get_favorite_by_name(
        self, user_id: int, guild_id: int, name: str
    ) -> Optional[Track]:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT track_data FROM favorites WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            row = await cursor.fetchone()
        if row:
            try:
                return Track.from_json(row["track_data"])
            except Exception:
                return None
        return None

    async def count_favorites(self, user_id: int, guild_id: int) -> int:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ── Queue Bookmarks (Tier-S Feature 5) ────────────────────────────────────

    MAX_BOOKMARKS_PER_USER = 20

    async def save_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
        tracks:   list[Track],
    ) -> bool:
        """
        Save a queue snapshot as a named bookmark.
        Returns True on success, False if the name already exists or limit reached.
        """
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM queue_bookmarks WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] >= self.MAX_BOOKMARKS_PER_USER:
                return False

            tracks_json = json.dumps(
                [t.to_dict() for t in tracks], ensure_ascii=False
            )
            try:
                await conn.execute(
                    """
                    INSERT INTO queue_bookmarks (user_id, guild_id, name, tracks_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, guild_id, name.strip()[:50], tracks_json),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False  # name collision

    async def load_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
    ) -> Optional[list[Track]]:
        """Load a bookmark by name. Returns list of Tracks or None if not found."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT tracks_json FROM queue_bookmarks WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["tracks_json"])
            return [Track.from_dict(d) for d in data]
        except Exception:
            return None

    async def list_bookmarks(
        self,
        user_id:  int,
        guild_id: int,
    ) -> list[dict]:
        """List all bookmarks for a user. Returns list of {name, count, created_at}."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                """
                SELECT name, tracks_json, created_at
                FROM queue_bookmarks
                WHERE user_id = ? AND guild_id = ?
                ORDER BY created_at DESC
                """,
                (user_id, guild_id),
            )
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            try:
                count = len(json.loads(row["tracks_json"]))
            except Exception:
                count = 0
            result.append({
                "name":       row["name"],
                "count":      count,
                "created_at": row["created_at"],
            })
        return result

    async def delete_bookmark(
        self,
        user_id:  int,
        guild_id: int,
        name:     str,
    ) -> bool:
        """Delete a bookmark by name. Returns True if it existed."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM queue_bookmarks WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            await conn.commit()
            return cursor.rowcount > 0

    # ── Session State (Tier-S+ Feature 11: Resume Position) ───────────────────

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
        Upsert full playback state for a guild.
        Called on graceful shutdown AND every 60s as a heartbeat.
        """
        now_json    = json.dumps(now_playing.to_dict(), ensure_ascii=False) if now_playing else None
        queue_json  = json.dumps([t.to_dict() for t in queue], ensure_ascii=False)
        effect_json = json.dumps(effects, ensure_ascii=False)

        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT INTO session_state
                    (guild_id, channel_id, text_channel_id, now_playing, elapsed_secs,
                     queue_json, loop_mode, volume, effects_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id      = excluded.channel_id,
                    text_channel_id = excluded.text_channel_id,
                    now_playing     = excluded.now_playing,
                    elapsed_secs    = excluded.elapsed_secs,
                    queue_json      = excluded.queue_json,
                    loop_mode       = excluded.loop_mode,
                    volume          = excluded.volume,
                    effects_json    = excluded.effects_json,
                    saved_at        = CURRENT_TIMESTAMP
                """,
                (guild_id, channel_id, text_channel_id, now_json, elapsed_secs,
                 queue_json, loop_mode, volume, effect_json),
            )
            await conn.commit()

    async def load_session_state(self, guild_id: int) -> Optional[dict]:
        """
        Load saved session state for a guild.
        Returns dict with keys:
            channel_id, text_channel_id, now_playing (Track|None),
            elapsed_secs, queue (list[Track]), loop_mode (str),
            volume (float), effects (list[str])
        Returns None if no state is saved.
        """
        async with self._connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM session_state WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        try:
            now_playing = Track.from_json(row["now_playing"]) if row["now_playing"] else None
            queue_data  = json.loads(row["queue_json"] or "[]")
            queue       = [Track.from_dict(d) for d in queue_data]
            effects     = json.loads(row["effects_json"] or "[]")
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
        async with self._connect() as conn:
            await conn.execute("DELETE FROM session_state WHERE guild_id = ?", (guild_id,))
            await conn.commit()
