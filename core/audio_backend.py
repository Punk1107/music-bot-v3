# -*- coding: utf-8 -*-
"""
core/audio_backend.py — FFmpeg audio backend for Music Bot V3.

V3: LavalinkBackend stub has been REMOVED. FFmpeg is the only backend.
The AudioBackend ABC is retained so future backends can be added without
touching caller code, but AUDIO_BACKEND config key is no longer read.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from models.track import Track
    from models.enums import AudioEffect, AudioQuality

logger = logging.getLogger(__name__)


class AudioBackend(ABC):
    """Abstract interface for audio playback backends."""

    @abstractmethod
    async def play(
        self,
        voice_client:  discord.VoiceClient,
        stream_url:    str,
        ffmpeg_opts:   dict,
        after_callback,
    ) -> None:
        """Start playing audio on the given voice client."""
        ...

    @abstractmethod
    async def stop(self, voice_client: discord.VoiceClient) -> None:
        """Stop the currently playing audio."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class FFmpegBackend(AudioBackend):
    """
    Discord.py's built-in FFmpegPCMAudio + PCMVolumeTransformer.

    This is the active, production-grade backend.
    """

    @property
    def name(self) -> str:
        return "ffmpeg"

    async def play(
        self,
        voice_client:  discord.VoiceClient,
        stream_url:    str,
        ffmpeg_opts:   dict,
        after_callback,
    ) -> None:
        """
        Create an FFmpegPCMAudio source and start playback.

        ffmpeg_opts must contain 'before_options' and 'options' keys
        as returned by AudioEffectsProcessor.build_ffmpeg_options().
        """
        source = discord.FFmpegPCMAudio(
            stream_url,
            before_options=ffmpeg_opts.get("before_options", ""),
            options=ffmpeg_opts.get("options", "-vn"),
        )
        voice_client.play(source, after=after_callback)
        logger.debug("FFmpegBackend: playing %s…", stream_url[:80])

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()


def create_backend() -> AudioBackend:
    """Return the active audio backend (always FFmpeg in V3)."""
    return FFmpegBackend()
