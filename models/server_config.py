# -*- coding: utf-8 -*-
"""models/server_config.py — Per-guild configuration for Music Bot V3.

V3 additions:
  - dj_role_id: optional DJ role — only DJ+ can run destructive commands
  - request_channel_id: text channel that accepts bare queries as /play
  - auto_playlist: per-guild toggle for auto-fill from history
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from models.enums import AudioQuality


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
            "max_playlist_tracks": self.max_playlist_tracks,
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
            auto_playlist      = bool(data.get("auto_playlist", False)),
            auto_playlist_size = int(data.get("auto_playlist_size", 5)),
            max_playlist_tracks= int(data.get("max_playlist_tracks", 100)),
        )

    @classmethod
    def from_json(cls, s: str) -> "ServerConfig":
        return cls.from_dict(json.loads(s))

    @classmethod
    def default(cls, guild_id: int) -> "ServerConfig":
        return cls(guild_id=guild_id)
