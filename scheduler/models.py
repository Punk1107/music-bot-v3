# -*- coding: utf-8 -*-
"""
scheduler/models.py — Data models for Server Music Scheduler & Alarm Clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class ScheduledTask:
    id: int
    guild_id: int
    voice_channel_id: int
    text_channel_id: int
    user_id: int
    scheduled_time: str          # "HH:MM" or "HH:MM:SS"
    days_of_week: str = "daily"  # "daily", "weekdays", "once"
    query: str = ""
    is_active: bool = True
    last_run_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTask:
        return cls(
            id=int(data["id"]),
            guild_id=int(data["guild_id"]),
            voice_channel_id=int(data["voice_channel_id"]),
            text_channel_id=int(data["text_channel_id"]),
            user_id=int(data.get("user_id", 0)),
            scheduled_time=str(data["scheduled_time"]),
            days_of_week=str(data.get("days_of_week", "daily")),
            query=str(data.get("query", "")),
            is_active=bool(data.get("is_active", True)),
            last_run_at=data.get("last_run_at"),
            locked_by=data.get("locked_by"),
            created_at=data.get("created_at"),
        )

