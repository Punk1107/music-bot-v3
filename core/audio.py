# -*- coding: utf-8 -*-
"""
core/audio.py — FFmpeg argument builder & audio-effects processor for Music Bot V3.

V3: Only FFmpeg backend. Lavalink references removed entirely.

Tier A+ additions (F21-F25):
  - Playback speed via atempo chain (F21)
  - Pitch shift via asetrate + aresample + atempo correction (F22)
  - Crossfade fade-out/fade-in via afade (F23, applied at track transition)
  - Silence trim via silenceremove (F24)
  - Replay gain / normalize via dynaudnorm (F25)
"""

from __future__ import annotations

import math

from models.enums import AudioEffect, AudioQuality


# -- FFmpeg effect filter strings ----------------------------------------------

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


def _build_atempo_chain(speed: float) -> str:
    """
    Build an atempo filter chain for the given speed multiplier.

    atempo only accepts values in [0.5, 2.0] per filter instance.
    For values outside that we chain multiple atempo filters.
    Returns empty string if speed == 1.0.
    """
    speed = max(0.1, min(10.0, speed))
    if abs(speed - 1.0) < 0.001:
        return ""

    parts: list[str] = []
    remaining = speed

    # Decompose into [0.5, 2.0] factors
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining *= 2.0

    # Clamp remaining into safe range
    remaining = max(0.5, min(2.0, remaining))
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


def _build_pitch_filter(semitones: int) -> str:
    """
    Shift pitch by N semitones WITHOUT changing playback speed.

    Method: change sample rate -> resample to 48000 -> compensate speed via atempo.
    ratio = 2^(semitones/12)
    """
    if semitones == 0:
        return ""
    ratio = 2 ** (semitones / 12.0)
    new_rate = int(48000 * ratio)
    # atempo correction: to keep speed constant after asetrate, multiply atempo by 1/ratio
    tempo_correction = 1.0 / ratio
    tempo_chain = _build_atempo_chain(tempo_correction)
    base = f"asetrate={new_rate},aresample=48000"
    return f"{base},{tempo_chain}" if tempo_chain else base


class AudioEffectsProcessor:
    """Builds FFmpeg before_options / options for a given effect + quality + volume."""

    def build_ffmpeg_options(
        self,
        effects:         list[AudioEffect] = (),
        volume:          float             = 1.0,
        quality:         AudioQuality      = AudioQuality.HIGH,
        seek_sec:        int               = 0,
        seek_seconds:    int               = 0,    # alias for seek_sec (Feature 11)
        speed:           float             = 1.0,  # F21 Playback Speed
        pitch_semitones: int               = 0,    # F22 Pitch Shift
        crossfade_secs:  int               = 0,    # F23 Crossfade fade-out duration
        silence_trim:    bool              = False, # F24 Silence Trim
        replay_gain:     bool              = False, # F25 Replay Gain / Normalize
    ) -> dict:
        """
        Return a dict with keys `before_options` and `options` suitable for
        discord.py's FFmpegPCMAudio constructor.

        Args:
            effects:         Active effects to chain.
            volume:          0.0 - 2.0 playback volume.
            quality:         Target audio quality.
            seek_sec:        Start position in seconds (for seek/resume).
            speed:           Playback speed multiplier (0.5-2.0). Default 1.0.
            pitch_semitones: Semitone shift (-6 to +6). Default 0.
            crossfade_secs:  Duration (s) of fade-out at track end. 0 = disabled.
            silence_trim:    Remove leading/trailing silence if True.
            replay_gain:     Normalize loudness across tracks if True.
        """
        # Merge both seek aliases
        seek_sec = seek_sec or seek_seconds

        before_opts: list[str] = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
        if seek_sec > 0:
            before_opts += ["-ss", str(int(seek_sec))]

        # -- Build filter chain ------------------------------------------------
        filters: list[str] = []

        # F24: Silence trim -- strip leading/trailing silence
        if silence_trim:
            filters.append(
                "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB"
                ":stop_periods=-1:stop_silence=1:stop_threshold=-50dB"
            )

        # F22: Pitch shift (before speed so both are perceivable independently)
        pitch_filter = _build_pitch_filter(pitch_semitones)
        if pitch_filter:
            filters.append(pitch_filter)

        # F21: Playback speed (atempo chain, after pitch so they compose cleanly)
        speed_chain = _build_atempo_chain(speed)
        if speed_chain:
            filters.append(speed_chain)

        # Existing audio effects
        for eff in effects:
            f = _EFFECT_FILTERS.get(eff)
            if f:
                filters.append(f)

        # F25: Replay Gain / Normalize
        if replay_gain:
            filters.append("dynaudnorm=f=75:g=25:p=0.95")

        # Volume filter (always last so it applies cleanly)
        vol_clamped = max(0.0, min(2.0, volume))
        filters.append(f"volume={vol_clamped:.2f}")

        # F23: Crossfade fade-out -- add afade=t=out at end of filter chain
        if crossfade_secs > 0:
            filters.append(f"afade=t=out:st=0:d={crossfade_secs}")

        filter_str = ",".join(filters)
        # Note: do NOT add -b:a here. discord.FFmpegPCMAudio appends its own
        # output pipeline (-f s16le -ar 48000 -ac 2) internally. Adding -b:a
        # conflicts with that PCM output format and causes silent playback.
        options_parts = ["-vn", f"-af {filter_str}"]

        return {
            "before_options": " ".join(before_opts),
            "options":        " ".join(options_parts),
        }

    def build_crossfade_in_options(
        self,
        crossfade_secs: int,
        volume:         float        = 1.0,
        quality:        AudioQuality = AudioQuality.HIGH,
    ) -> dict:
        """
        Build FFmpeg options for the incoming track in a crossfade (F23).
        Adds a fade-in filter at the start of the track.
        """
        filters = [
            f"afade=t=in:st=0:d={crossfade_secs}",
            f"volume={max(0.0, min(2.0, volume)):.2f}",
        ]
        # Note: no -b:a here for the same reason as build_ffmpeg_options.
        return {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": f"-vn -af {','.join(filters)}",
        }
