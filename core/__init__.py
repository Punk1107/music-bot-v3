# -*- coding: utf-8 -*-
"""core/__init__.py — Core package for Music Bot V3."""
from core.database       import DatabaseManager
from core.youtube        import YouTubeExtractor
from core.spotify        import SpotifyExtractor
from core.audio          import AudioEffectsProcessor
from core.audio_backend  import AudioBackend, FFmpegBackend, create_backend
from core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from core.nlu            import NLUPipeline, RegexNLU
from core.player         import GuildPlayer

__all__ = [
    "DatabaseManager", "YouTubeExtractor", "SpotifyExtractor",
    "AudioEffectsProcessor", "AudioBackend", "FFmpegBackend", "create_backend",
    "CircuitBreaker", "CircuitBreakerOpen",
    "NLUPipeline", "RegexNLU",
    "GuildPlayer",
]
