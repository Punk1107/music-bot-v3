# -*- coding: utf-8 -*-
"""
core/audio.py — FFmpeg argument builder & audio-effects processor for Music Bot V3.

V3: Only FFmpeg backend. Lavalink references removed entirely.
"""

from __future__ import annotations

from models.enums import AudioEffect, AudioQuality


# ── FFmpeg effect filter strings ──────────────────────────────────────────────

_EFFECT_FILTERS: dict[AudioEffect, str] = {
    AudioEffect.BASS_BOOST:     "bass=g=15,dynaudnorm",
    AudioEffect.NIGHTCORE:      "asetrate=48000*1.25,aresample=48000,atempo=1.06",
    AudioEffect.VAPORWAVE:      "asetrate=48000*0.8,aresample=48000,atempo=1.1",
    AudioEffect.TREBLE_BOOST:   "treble=g=8",
    AudioEffect.VOCAL_BOOST:    "afftfilt=real='re * (f >= 300 && f <= 3000)'",
    AudioEffect.KARAOKE:        "pan=mono|c0=0.5*c0+-0.5*c1",
    AudioEffect.VIBRATO:        "vibrato=f=6.5:d=0.35",
    AudioEffect.TREMOLO:        "tremolo=f=8.8:d=0.6",
    AudioEffect.CHORUS:         "chorus=0.7:0.9:55:0.4:0.25:2",
    AudioEffect.REVERB:         "aecho=0.8:0.9:1000:0.3",
    AudioEffect.ECHO:           "aecho=0.8:0.88:60:0.4",
    AudioEffect.DISTORTION:     "afftfilt=real='hypot(re,im)*sin(0)'",
    AudioEffect.MONO:           "pan=mono|c0=0.5*c0+0.5*c1",
    AudioEffect.STEREO_ENHANCE: "extrastereo=m=2.5",
    AudioEffect.COMPRESSOR:     "acompressor=threshold=0.089:ratio=9:attack=200:release=1000",
    AudioEffect.LIMITER:        "alimiter=level_in=1:level_out=0.8:limit=0.8",
    AudioEffect.NOISE_GATE:     "agate=threshold=0.02:ratio=4:attack=10:release=200",
    AudioEffect.AUDIO_8D:       "apulsator=hz=0.125",
}


class AudioEffectsProcessor:
    """Builds FFmpeg before_options / options for a given effect + quality + volume."""

    def build_ffmpeg_options(
        self,
        effects:  list[AudioEffect] = (),
        volume:   float             = 1.0,
        quality:  AudioQuality      = AudioQuality.HIGH,
        seek_sec: int               = 0,
    ) -> dict:
        """
        Return a dict with keys `before_options` and `options` suitable for
        discord.py's FFmpegPCMAudio constructor.

        Args:
            effects:  Active effects to chain.
            volume:   0.0 – 2.0 playback volume.
            quality:  Target audio quality.
            seek_sec: Start position in seconds (for seek/resume).
        """
        before_opts: list[str] = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
        if seek_sec > 0:
            before_opts += ["-ss", str(int(seek_sec))]

        # ── Build filter chain ────────────────────────────────────────────────
        filters: list[str] = []

        for eff in effects:
            f = _EFFECT_FILTERS.get(eff)
            if f:
                filters.append(f)

        # Volume filter
        vol_clamped = max(0.0, min(2.0, volume))
        filters.append(f"volume={vol_clamped:.2f}")

        filter_str = ",".join(filters)
        options_parts = ["-vn", f"-af {filter_str}"]

        # Quality bitrate
        bitrate_map = {
            AudioQuality.LOW:    "64k",
            AudioQuality.MEDIUM: "128k",
            AudioQuality.HIGH:   "192k",
            AudioQuality.ULTRA:  "320k",
        }
        options_parts.append(f"-b:a {bitrate_map.get(quality, '192k')}")

        return {
            "before_options": " ".join(before_opts),
            "options":        " ".join(options_parts),
        }
