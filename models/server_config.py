# -*- coding: utf-8 -*-
"""models/server_config.py — Per-guild configuration for Music Bot V3.

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
  - language: UI locale for this guild ("en" / "th") — F30 Localization
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from models.enums import AudioQuality, QueuePermission, DuplicateMode, EmbedTheme


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

    # V3 NEW: Auto-playlist
    auto_playlist: bool = False
    auto_playlist_size: int = 5

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
    language: str = "en"  # "en" or "th"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "guild_id":            self.guild_id,
            "volume":              self.volume,
            "audio_quality":       self.audio_quality.value,
            "max_track_len":       self.max_track_len,
            "idle_timeout":        self.idle_timeout,
            "dj_only":             self.dj_only,
            "dj_role_id":          self.dj_role_id,
            "request_channel_id":  self.request_channel_id,
            "auto_playlist":       self.auto_playlist,
            "auto_playlist_size":  self.auto_playlist_size,
            "max_playlist_tracks":    self.max_playlist_tracks,
            "queue_locked":           self.queue_locked,
            "queue_add_permission":   self.queue_add_permission.value,
            "duplicate_mode":         self.duplicate_mode.value,
            # Tier A/B
            "auto_leave_alone":       self.auto_leave_alone,
            "auto_pause_empty":       self.auto_pause_empty,
            "embed_theme":            self.embed_theme.value,
            "guild_presets":          self.guild_presets,
            # Tier Performance
            "language":               self.language,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "ServerConfig":
        return cls(
            guild_id           = int(data.get("guild_id", 0)),
            volume             = float(data.get("volume", 1.0)),
            audio_quality      = AudioQuality(data.get("audio_quality", "high")),
            max_track_len      = int(data.get("max_track_len", 7200)),
            idle_timeout       = int(data.get("idle_timeout", 300)),
            dj_only            = bool(data.get("dj_only", False)),
            dj_role_id         = data.get("dj_role_id"),
            request_channel_id = data.get("request_channel_id"),
            auto_playlist         = bool(data.get("auto_playlist", False)),
            auto_playlist_size    = int(data.get("auto_playlist_size", 5)),
            max_playlist_tracks   = int(data.get("max_playlist_tracks", 100)),
            queue_locked          = bool(data.get("queue_locked", False)),
            queue_add_permission  = QueuePermission(data.get("queue_add_permission", "everyone")),
            duplicate_mode        = DuplicateMode(data.get("duplicate_mode", "allow")),
            # Tier A/B
            auto_leave_alone      = bool(data.get("auto_leave_alone", True)),
            auto_pause_empty      = bool(data.get("auto_pause_empty", True)),
            embed_theme           = EmbedTheme(data.get("embed_theme", "classic")),
            guild_presets         = data.get("guild_presets", {}),
            # Tier Performance
            language              = data.get("language", "en"),
        )

    @classmethod
    def from_json(cls, s: str) -> "ServerConfig":
        return cls.from_dict(json.loads(s))

    @classmethod
    def default(cls, guild_id: int) -> "ServerConfig":
        return cls(guild_id=guild_id)
