# -*- coding: utf-8 -*-
"""models/server_config.py — Per-guild configuration for Music Bot V3 (Gen 5.0 Normalized MySQL).

V3 additions:
  - dj_role_id: optional DJ role — only DJ+ can run destructive commands
  - request_channel_id: text channel that accepts bare queries as /play
  - auto_playlist: per-guild toggle for auto-fill from history

Tier-S additions:
  - queue_locked: prevents non-DJ/Admin from adding to queue (Feature 2)
  - queue_add_permission: granular permission level for adding tracks (Feature 3)
  - duplicate_mode: how duplicate URLs are handled (Feature 6)

Tier A/A+/B additions:
  - auto_leave_alone, auto_pause_empty: voice channel behaviour (F17, F18)
  - embed_theme: visual theme for embeds (F27)
  - guild_presets: saved preset bundles (F26)

Tier Performance additions:
  - language: UI locale for this guild ("en" / "th" / etc.) — F30 Localization

Gen 5.0 MySQL additions:
  - timezone: Guild timezone string (default "UTC") for scheduled tasks
  - from_db_row() / to_db_params(): Direct mapping to normalized MySQL server_configs columns
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from models.enums import AudioQuality, QueuePermission, DuplicateMode, EmbedTheme


def _safe_enum(enum_cls: Any, val: Any, default: Any) -> Any:
    try:
        return enum_cls(val)
    except Exception:
        return default


@dataclass
class ServerConfig:
    guild_id: int

    # Audio
    volume:        float = 1.0
    audio_quality: AudioQuality = AudioQuality.HIGH
    max_track_len: int = 7200       # seconds

    # Behaviour
    idle_timeout:  int = 300        # seconds before auto-disconnect
    dj_only:       bool = False     # require DJ role for control commands

    # V3 NEW: DJ role
    dj_role_id:    Optional[int] = None  # None = anyone can control

    # V3 NEW: Request channel
    request_channel_id: Optional[int] = None

    # Gen 5.0 NEW: Guild timezone for scheduled playback tasks
    timezone: str = "UTC"

    # V3 NEW: Auto-playlist
    auto_playlist: bool = False
    auto_playlist_size: int = 5

    # Feature 1.4: Smart Autoplay
    smart_autoplay: bool = False

    # Playlist import limit
    max_playlist_tracks: int = 100

    # Tier-S: Queue Lock (Feature 2)
    queue_locked: bool = False

    # Tier-S: Queue Permission Level (Feature 3)
    queue_add_permission: QueuePermission = QueuePermission.EVERYONE

    # Tier-S: Duplicate Detection Mode (Feature 6)
    duplicate_mode: DuplicateMode = DuplicateMode.ALLOW

    # ── Tier A: Voice Channel Behaviour ──────────────────────────────────────
    # F17: Auto-leave when bot is the only member for >90s
    auto_leave_alone: bool = True
    # F18: Auto-pause when channel is empty; resume on rejoin
    auto_pause_empty: bool = True

    # ── Tier B: Theme System (F27) ────────────────────────────────────────────
    embed_theme: EmbedTheme = EmbedTheme.CLASSIC

    # ── Tier B: Guild Presets (F26) ───────────────────────────────────────────
    # Stored as {preset_name: {effects, volume, quality, loop, speed, pitch, crossfade}}
    guild_presets: Dict[str, Any] = field(default_factory=dict)

    # ── Tier Performance: Localization (F30) ──────────────────────────────────
    language: str = "en"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "guild_id":             self.guild_id,
            "volume":               self.volume,
            "audio_quality":        self.audio_quality.value if hasattr(self.audio_quality, "value") else str(self.audio_quality),
            "max_track_len":        self.max_track_len,
            "idle_timeout":         self.idle_timeout,
            "dj_only":              self.dj_only,
            "dj_role_id":           self.dj_role_id,
            "request_channel_id":   self.request_channel_id,
            "timezone":             self.timezone,
            "auto_playlist":        self.auto_playlist,
            "auto_playlist_size":   self.auto_playlist_size,
            "smart_autoplay":       self.smart_autoplay,
            "max_playlist_tracks":  self.max_playlist_tracks,
            "queue_locked":         self.queue_locked,
            "queue_add_permission": self.queue_add_permission.value if hasattr(self.queue_add_permission, "value") else str(self.queue_add_permission),
            "duplicate_mode":       self.duplicate_mode.value if hasattr(self.duplicate_mode, "value") else str(self.duplicate_mode),
            # Tier A/B
            "auto_leave_alone":     self.auto_leave_alone,
            "auto_pause_empty":     self.auto_pause_empty,
            "embed_theme":          self.embed_theme.value if hasattr(self.embed_theme, "value") else str(self.embed_theme),
            "guild_presets":        self.guild_presets,
            # Tier Performance
            "language":             self.language,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_db_params(self) -> tuple:
        """
        Return tuple of 19 normalized column values for MySQL `server_configs` table:
        (guild_id, volume, audio_quality, max_track_len, idle_timeout,
         dj_only, dj_role_id, request_channel_id, timezone,
         auto_playlist, auto_playlist_size, smart_autoplay,
         queue_locked, queue_add_permission, duplicate_mode,
         auto_leave_alone, auto_pause_empty, embed_theme, language, guild_presets_json)
        """
        presets_payload = dict(self.guild_presets or {})
        if self.max_playlist_tracks != 100:
            presets_payload["_max_playlist_tracks"] = self.max_playlist_tracks
        presets_json = json.dumps(presets_payload, ensure_ascii=False) if presets_payload else None

        return (
            int(self.guild_id),
            float(self.volume),
            self.audio_quality.value if hasattr(self.audio_quality, "value") else str(self.audio_quality),
            int(self.max_track_len),
            int(self.idle_timeout),
            bool(self.dj_only),
            int(self.dj_role_id) if self.dj_role_id is not None else None,
            int(self.request_channel_id) if self.request_channel_id is not None else None,
            str(self.timezone or "UTC")[:64],
            bool(self.auto_playlist),
            int(self.auto_playlist_size),
            bool(self.smart_autoplay),
            bool(self.queue_locked),
            self.queue_add_permission.value if hasattr(self.queue_add_permission, "value") else str(self.queue_add_permission),
            self.duplicate_mode.value if hasattr(self.duplicate_mode, "value") else str(self.duplicate_mode),
            bool(self.auto_leave_alone),
            bool(self.auto_pause_empty),
            self.embed_theme.value if hasattr(self.embed_theme, "value") else str(self.embed_theme),
            str(self.language or "en")[:8],
            presets_json,
        )

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ServerConfig":
        """
        Construct ServerConfig from a MySQL `server_configs` normalized row
        (or legacy SQLite `config_data` row if present).
        """
        if "config_data" in row and row["config_data"] and "audio_quality" not in row:
            raw_cfg = row["config_data"]
            if isinstance(raw_cfg, str):
                return cls.from_json(raw_cfg)
            if isinstance(raw_cfg, dict):
                return cls.from_dict(raw_cfg)

        raw_presets = row.get("guild_presets")
        if isinstance(raw_presets, str) and raw_presets.strip():
            try:
                presets = json.loads(raw_presets)
            except Exception:
                presets = {}
        elif isinstance(raw_presets, dict):
            presets = dict(raw_presets)
        else:
            presets = {}

        max_playlist_tracks = int(row.get("max_playlist_tracks", presets.pop("_max_playlist_tracks", 100)))

        return cls(
            guild_id             = int(row.get("guild_id", 0)),
            volume               = float(row.get("volume", 1.0)),
            audio_quality        = _safe_enum(AudioQuality, row.get("audio_quality", "high"), AudioQuality.HIGH),
            max_track_len        = int(row.get("max_track_len", 7200)),
            idle_timeout         = int(row.get("idle_timeout", 300)),
            dj_only              = bool(row.get("dj_only", False)),
            dj_role_id           = int(row["dj_role_id"]) if row.get("dj_role_id") is not None else None,
            request_channel_id   = int(row["request_channel_id"]) if row.get("request_channel_id") is not None else None,
            timezone             = str(row.get("timezone") or "UTC"),
            auto_playlist        = bool(row.get("auto_playlist", False)),
            auto_playlist_size   = int(row.get("auto_playlist_size", 5)),
            smart_autoplay       = bool(row.get("smart_autoplay", False)),
            max_playlist_tracks  = max_playlist_tracks,
            queue_locked         = bool(row.get("queue_locked", False)),
            queue_add_permission = _safe_enum(QueuePermission, row.get("queue_add_permission", "everyone"), QueuePermission.EVERYONE),
            duplicate_mode       = _safe_enum(DuplicateMode, row.get("duplicate_mode", "allow"), DuplicateMode.ALLOW),
            auto_leave_alone     = bool(row.get("auto_leave_alone", True)),
            auto_pause_empty     = bool(row.get("auto_pause_empty", True)),
            embed_theme          = _safe_enum(EmbedTheme, row.get("embed_theme", "classic"), EmbedTheme.CLASSIC),
            guild_presets        = presets,
            language             = str(row.get("language") or "en"),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ServerConfig":
        return cls.from_db_row(data)

    @classmethod
    def from_json(cls, s: str | dict) -> "ServerConfig":
        if isinstance(s, dict):
            return cls.from_dict(s)
        return cls.from_dict(json.loads(s))

    @classmethod
    def default(cls, guild_id: int) -> "ServerConfig":
        return cls(guild_id=guild_id)
