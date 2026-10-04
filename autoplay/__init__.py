# -*- coding: utf-8 -*-
"""
autoplay/ — Smart Autoplay (Feature 1.4) for Music Bot V3.

Exports:
  AutoplayService  — YouTube Related Videos extraction + per-guild history dedup
  AutoplayCog      — /autoplay slash command

Usage in main.py:
  bot.autoplay = AutoplayService(bot.youtube)
  await bot.load_extension("autoplay.cog")
"""

from autoplay.service import AutoplayService, extract_video_id
from autoplay.cog import AutoplayCog

__all__ = ["AutoplayService", "AutoplayCog", "extract_video_id"]

