# -*- coding: utf-8 -*-
"""models/enums.py — Enumerations for Music Bot V3."""

from __future__ import annotations

from enum import Enum


class LoopMode(str, Enum):
    """Playback loop mode."""
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"

    def label(self) -> str:
        return {"off": "🔁 Loop: Off", "track": "🔂 Loop: Track", "queue": "🔁 Loop: Queue"}[self.value]

    def next(self) -> "LoopMode":
        order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
        return order[(order.index(self) + 1) % len(order)]


class AudioEffect(str, Enum):
    """Supported FFmpeg audio effects."""
    BASS_BOOST     = "bass_boost"
    NIGHTCORE      = "nightcore"
    VAPORWAVE      = "vaporwave"
    TREBLE_BOOST   = "treble_boost"
    VOCAL_BOOST    = "vocal_boost"
    KARAOKE        = "karaoke"
    VIBRATO        = "vibrato"
    TREMOLO        = "tremolo"
    CHORUS         = "chorus"
    REVERB         = "reverb"
    ECHO           = "echo"
    DISTORTION     = "distortion"
    MONO           = "mono"
    STEREO_ENHANCE = "stereo_enhance"
    COMPRESSOR     = "compressor"
    LIMITER        = "limiter"
    NOISE_GATE     = "noise_gate"
    AUDIO_8D       = "8d_audio"

    def display_name(self) -> str:
        return {
            "bass_boost":     "🎸 Bass Boost",
            "nightcore":      "🌙 Nightcore",
            "vaporwave":      "🌊 Vaporwave",
            "treble_boost":   "🔺 Treble Boost",
            "vocal_boost":    "🎤 Vocal Boost",
            "karaoke":        "🎤 Karaoke",
            "vibrato":        "〰️ Vibrato",
            "tremolo":        "〰️ Tremolo",
            "chorus":         "🎼 Chorus",
            "reverb":         "🏛️ Reverb",
            "echo":           "📢 Echo",
            "distortion":     "⚡ Distortion",
            "mono":           "🔊 Mono",
            "stereo_enhance": "🎧 Stereo Enhance",
            "compressor":     "🔧 Compressor",
            "limiter":        "🚧 Limiter",
            "noise_gate":     "🔇 Noise Gate",
            "8d_audio":       "🔄 8D Audio",
        }[self.value]


class AudioQuality(str, Enum):
    """Audio quality presets."""
    LOW    = "low"     # 64 kbps
    MEDIUM = "medium"  # 128 kbps
    HIGH   = "high"    # 192 kbps
    ULTRA  = "ultra"   # 320 kbps (best available)

    def ffmpeg_options(self) -> dict:
        bitrates = {"low": "64k", "medium": "128k", "high": "192k", "ultra": "320k"}
        return {"options": f"-vn -ab {bitrates[self.value]}"}


class NLUIntent(str, Enum):
    """Intents recognized by the Regex NLU engine (V3)."""
    PLAY    = "play"
    PAUSE   = "pause"
    RESUME  = "resume"
    SKIP    = "skip"
    STOP    = "stop"
    VOLUME  = "volume"
    QUEUE   = "queue"
    LOOP    = "loop"
    SHUFFLE = "shuffle"
    UNKNOWN = "unknown"
