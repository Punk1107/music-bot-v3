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
from typing import Optional

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
    AudioEffect.DISTORTION:     "acrusher=level_in=1:level_out=1:bits=8:mode=log:aa=1",
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
    if not isinstance(speed, (int, float)) or math.isnan(speed) or math.isinf(speed):
        speed = 1.0
    speed = max(0.1, min(10.0, float(speed)))
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


# -- Synthetic Audio Chimes (Gen 5.0) -------------------------------------------
SYNTHETIC_CHIME_EXPR = (
    "0.15*sin(2*PI*880*t)*exp(-4*t)+0.15*sin(2*PI*1760*t)*exp(-6*t):d=0.35:s=48000:c=stereo"
)


def build_chime_lavfi_source() -> str:
    """Return lavfi expression for synthetic harmonic notification chime."""
    return f"aevalsrc={SYNTHETIC_CHIME_EXPR}"


def build_chime_ffmpeg_options() -> dict:
    """Return options dict for playing synthetic chime with discord.FFmpegPCMAudio."""
    return {
        "before_options": "-nostdin -f lavfi",
        "options": "-vn",
    }


class AudioEffectsProcessor:
    """Builds FFmpeg before_options / options for a given effect + quality + volume."""

    def build_ffmpeg_options(
        self,
        effects:          list[AudioEffect] = (),
        volume:           float             = 1.0,
        quality:          AudioQuality      = AudioQuality.HIGH,
        seek_sec:         int               = 0,
        seek_seconds:     int               = 0,     # alias for seek_sec (Feature 11)
        speed:            float             = 1.0,   # F21 Playback Speed
        pitch_semitones:  int               = 0,     # F22 Pitch Shift
        crossfade_secs:   int               = 0,     # F23 Crossfade fade-out duration
        silence_trim:     bool              = False, # F24 Silence Trim
        replay_gain:      bool              = False, # F25 Replay Gain / Normalize
        equalizer_filter: Optional[str]     = None,  # F2.5 Frequency Equalizer
        loudnorm:         bool              = False, # F2.6 EBU R128 Loudness Normalization
        pan_filter:       Optional[str]     = None,  # F2.8 Left-Right Audio Balance
        stereo_filter:    Optional[str]     = None,  # F2.8 Stereo Enhancer / Wide Soundstage
        to_seconds:       Optional[int]     = None,  # F2.7 Loop A-B end cutoff timestamp
        track_duration:   Optional[int]     = None,  # Total track duration for crossfade
        vocal_filter:     Optional[str]     = None,  # Gen 5.0 Mid-Side Vocal Remover / Acapella
        spatial_filter:   Optional[str]     = None,  # Gen 5.0 Spatial 3D / Binaural
        remaster_filter:  Optional[str]     = None,  # Gen 5.0 Clarity & Sub-Harmonic Remaster
        loudnorm_filter:  Optional[str]     = None,  # Gen 5.0 Multi-Profile EBU R128 Loudnorm
        gapless:          bool              = False, # Gen 5.0 Gapless low-latency buffering
    ) -> dict:
        """
        Return a dict with keys `before_options` and `options` suitable for
        discord.py's FFmpegPCMAudio constructor.
        """
        # Merge both seek aliases
        seek_sec = seek_sec or seek_seconds

        probesize = "32768" if gapless else "65536"
        delay_max = "2" if gapless else "5"
        before_opts: list[str] = [
            "-nostdin",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", delay_max,
            "-reconnect_on_network_error", "1",
            "-reconnect_on_http_error", "4xx,5xx",
            "-probesize", probesize,
            "-analyzeduration", "0",
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

        # Gen 5.0: Mid-Side Vocal Remover / Acapella (applied early before reverb/echo)
        if vocal_filter:
            filters.append(vocal_filter)

        # F2.5: Frequency Equalizer
        if equalizer_filter:
            filters.append(equalizer_filter)

        # Gen 5.0: Spatial 3D & Binaural Virtualizer
        if spatial_filter:
            filters.append(spatial_filter)

        # F2.8: Audio Pan (balance)
        if pan_filter:
            filters.append(pan_filter)

        # F2.8: Stereo Widening / Soundstage Enhance
        if stereo_filter:
            filters.append(stereo_filter)

        # Existing audio effects
        for eff in effects:
            f = _EFFECT_FILTERS.get(eff)
            if f:
                filters.append(f)

        # Gen 5.0: Audio Clarity & Sub-Harmonic Remaster
        if remaster_filter:
            filters.append(remaster_filter)

        # F2.6 / Gen 5.0: EBU R128 Loudnorm (Multi-profile or standard, priority over legacy dynaudnorm)
        if loudnorm_filter:
            filters.append(loudnorm_filter)
        elif loudnorm:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        elif replay_gain:
            # F25: Legacy Replay Gain / Normalize
            filters.append("dynaudnorm=f=75:g=25:p=0.95")

        # Volume filter (only when non-unity; unity volume is handled directly by PCMVolumeTransformer)
        if not isinstance(volume, (int, float)) or math.isnan(volume) or math.isinf(volume):
            vol_clamped = 1.0
        else:
            vol_clamped = max(0.0, min(2.0, float(volume)))
        if abs(vol_clamped - 1.0) > 0.001:
            filters.append(f"volume={vol_clamped:.2f}")

        # F23: Crossfade fade-out -- only apply at track end if duration is known
        crossfade_secs = max(0, int(crossfade_secs)) if isinstance(crossfade_secs, (int, float)) and not math.isnan(crossfade_secs) and not math.isinf(crossfade_secs) else 0
        if crossfade_secs > 0 and track_duration and track_duration > crossfade_secs:
            fade_start = max(0, int(track_duration - crossfade_secs))
            filters.append(f"afade=t=out:st={fade_start}:d={crossfade_secs}")

        filter_str = ",".join(filters)
        # Note: do NOT add -b:a here. discord.FFmpegPCMAudio appends its own
        # output pipeline (-f s16le -ar 48000 -ac 2) internally. Adding -b:a
        # conflicts with that PCM output format and causes silent playback.
        options_parts = ["-vn"]
        if filter_str:
            options_parts.append(f"-af {filter_str}")
        if to_seconds is not None and to_seconds > 0:
            options_parts.append(f"-to {int(to_seconds)}")

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
        if not isinstance(volume, (int, float)) or math.isnan(volume) or math.isinf(volume):
            vol_clamped = 1.0
        else:
            vol_clamped = max(0.0, min(2.0, float(volume)))
        fade_secs = max(0, int(crossfade_secs)) if isinstance(crossfade_secs, (int, float)) and not math.isnan(crossfade_secs) and not math.isinf(crossfade_secs) else 0
        filters = [
            f"afade=t=in:st=0:d={fade_secs}",
            f"volume={vol_clamped:.2f}",
        ]
        # Note: no -b:a here for the same reason as build_ffmpeg_options.
        return {
            "before_options": "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -probesize 65536 -analyzeduration 0",
            "options": f"-vn -af {','.join(filters)}",
        }
