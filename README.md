# 🎵 Music Bot V3

A professional, production-ready Discord music bot built in Python.
Engineered with a clean modular architecture, enterprise-grade stability patterns,
and **7 new features** in V3 — all self-contained, no third-party services.

---

## ✨ What's New in V3

| Feature | Details |
|---------|---------|
| ❤️ **Favorites System** | Save, list, and instantly play your favorite tracks per user |
| 🎚️ **DJ Role** | Restrict destructive commands to a designated DJ role |
| 📻 **Request Channel** | Dedicate a text channel where typing a song name triggers playback |
| 📊 **Live Progress Bar** | Now-playing embed shows `████░░ 2:35/4:12` + auto-updates every 30s |
| 🔤 **Regex NLU** | EN+TH intent detection (no OpenAI/Anthropic — zero external API) |
| 📈 **REST API + WebSocket** | `/api/v1/` endpoints + real-time WebSocket dashboard |
| 🎼 **Auto-Playlist** | Fills queue from history when it empties — configurable per guild |

## ✨ Full Feature List

| Feature | Details |
|---------|---------|
| 🎵 **YouTube Playback** | URL or search keywords; smart autocomplete from history |
| 🎤 **Spotify Support** | Track, album, full playlist → parallel-resolved to YouTube |
| 📋 **Smart Queue** | Persistent to SQLite, paginated & interactive dropdown management |
| 🔁 **Loop Modes** | Off → Track → Queue via button or command |
| 🎛 **18 Audio Effects** | Bass Boost, Nightcore, Vaporwave, Treble, Vocal, Karaoke, Vibrato, Tremolo, Chorus, Reverb, Echo, Distortion, Mono, Stereo Enhance, Compressor, Limiter, Noise Gate, 8D Audio |
| 🔊 **Volume Control** | 0–200% via `/volume` or ±10% buttons |
| 🎮 **Interactive UI** | Dynamic skip counts, disabled states, ❤️ favorite button on now-playing |
| 🎨 **Dynamic Colors** | Dominant color auto-extracted from thumbnails (pure Python, no Pillow) |
| 📊 **Analytics** | Per-guild play history, per-user stats, REST API `/api/v1/guild/{id}/analytics` |
| 🛡 **Content Filter** | 7-stage pipeline — blocks NSFW/gambling/piracy (EN+TH) |
| 💤 **Idle Auto-disconnect** | Configurable per-guild timeout |
| 🔄 **Self-healing Voice** | Exponential-backoff reconnect (2s→4s→8s) |
| ⚡ **Circuit Breakers** | 3-state (CLOSED/OPEN/HALF-OPEN) with metrics on dashboard |
| ⏩ **Predictive Pre-fetch** | Next track CDN URL resolved ~15s before current ends |
| 🔌 **FFmpeg Only** | Lavalink removed — clean, minimal, battle-tested |

---

## 📁 Project Structure

```text
music-bot-v3/
├── main.py           # MusicBot, events, 5 background tasks
├── config.py         # All settings from .env
├── webserver.py      # aiohttp REST API + WebSocket dashboard
├── requirements.txt  # Python dependencies
├── Dockerfile        # Container build
├── docker-compose.yml
│
├── cogs/
│   ├── music.py      # /join /leave /play /search /pause /resume /skip /stop /nowplaying
│   ├── queue_cog.py  # /queue /shuffle /clear /loop /remove /move
│   ├── effects.py    # /volume /effects /effects_list /effects_clear /quality
│   ├── info.py       # /history /stats /botstats /help
│   ├── favorites.py  # /favorite add/list/play/remove  [NEW]
│   └── admin.py      # /djset /requestchannel /autoplaylist /idletimeout  [NEW]
│
├── core/
│   ├── database.py      # aiosqlite + favorites table + analytics
│   ├── youtube.py       # yt-dlp wrapper + cache prune
│   ├── spotify.py       # Spotify → YouTube resolver
│   ├── audio.py         # FFmpeg filter chain (18 effects)
│   ├── audio_backend.py # FFmpegBackend (Lavalink removed)
│   ├── circuit_breaker.py # With metrics
│   ├── nlu.py           # Regex NLU (no LLM)  [REPLACED]
│   ├── player.py        # GuildPlayer with auto_playlist_mode
│   └── validator.py     # 7-stage URL safety
│
├── models/
│   ├── track.py         # Track dataclass + is_favorite
│   ├── server_config.py # dj_role_id, request_channel_id, auto_playlist
│   └── enums.py         # LoopMode, AudioEffect, AudioQuality, NLUIntent
│
└── utils/
    ├── embeds.py        # Progress bar, favorites, DJ, auto-playlist embeds
    ├── views.py         # MusicControlView (❤️ button) + FavoritesView
    ├── color_thief.py   # CPU in executor
    ├── formatters.py    # make_progress_bar()
    ├── rate_limiter.py  # Sliding-window limiter
    └── error_handler.py # Bilingual EN+TH errors
```

---

## 🚀 Quick Start

### Prerequisites
- Python **3.10+**
- [FFmpeg](https://ffmpeg.org/download.html) on your `PATH`

### Install
```bash
cd music-bot-v3
pip install -r requirements.txt
cp .env.example .env
# Fill in DISCORD_TOKEN and APP_ID
python main.py
```

### Docker
```bash
cp .env.example .env
# Fill in your secrets
docker-compose up -d
```

---

## 🎛 Commands

### Playback
| Command | Description |
|---------|-------------|
| `/join` | Join your voice channel |
| `/leave` | Disconnect and clear queue |
| `/play <query>` | YouTube URL, Spotify URL, playlist, or search |
| `/search <query>` | Search and choose from dropdown |
| `/pause` / `/resume` | Pause/resume |
| `/skip` | Skip current track |
| `/stop` | Stop and disconnect |
| `/nowplaying` | Show live progress bar embed |

### Queue
| Command | Description |
|---------|-------------|
| `/queue [page]` | Paginated queue |
| `/shuffle` | Shuffle queue |
| `/clear` | Clear entire queue |
| `/loop` | Cycle Off → Track → Queue |
| `/remove <pos>` | Remove by position |
| `/move <from> <to>` | Reorder atomically |

### Audio
| Command | Description |
|---------|-------------|
| `/volume <0-200>` | Set volume |
| `/effects <name>` | Toggle an effect |
| `/effects_list` | Show all 18 effects |
| `/effects_clear` | Clear all effects |
| `/quality <preset>` | low / medium / high / ultra |

### ❤️ Favorites (V3)
| Command | Description |
|---------|-------------|
| `/favorite add [name]` | Save current track |
| `/favorite list [user]` | View favorites |
| `/favorite play <name>` | Play a favorite |
| `/favorite remove <name>` | Delete a favorite |

### ⚙️ Admin (V3)
| Command | Description |
|---------|-------------|
| `/djset role @role` | Set DJ role |
| `/djset clear` | Remove DJ restriction |
| `/requestchannel set #ch` | Set request channel |
| `/requestchannel clear` | Remove request channel |
| `/autoplaylist on/off` | Toggle auto-playlist |
| `/idletimeout <secs>` | Set idle disconnect timer |

### Info
| Command | Description |
|---------|-------------|
| `/history [user]` | Play history |
| `/stats [user]` | Listening statistics |
| `/botstats` | Bot performance metrics |
| `/help` | Full command list |

---

## 🌐 REST API (V3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/ready` | GET | Ready check |
| `/status` | GET | Full status JSON |
| `/api/v1/guilds` | GET | Active guilds list |
| `/api/v1/guild/{id}/nowplaying` | GET | Current track + progress |
| `/api/v1/guild/{id}/queue` | GET | Full queue JSON |
| `/api/v1/guild/{id}/analytics?days=7` | GET | Play analytics |
| `/ws/stats` | WebSocket | Real-time stats push every 5s |

Set `API_SECRET=yourtoken` in `.env` and pass `Authorization: Bearer yourtoken`.

---

## 🔄 V2 → V3 Changes

| Component | V2 | V3 |
|-----------|----|----|
| NLU | OpenAI/Anthropic (ext.) | Regex engine (internal) |
| Audio Backend | FFmpeg + Lavalink stub | FFmpeg only |
| Database | aiosqlite + WAL | + favorites table + immediate saves |
| Webserver | `/health /status /ready` | + full REST API v1 + WebSocket |
| Now Playing | Static embed | Live progress bar (updates every 30s) |
| Queue Save | Every 5 min | Write-ahead on enqueue + every 5 min |
| Color Thief | Blocking | Thread executor |
| Cache Prune | Never | Every 30 min |
| New: Favorites | ❌ | ✅ |
| New: DJ Role | ❌ | ✅ |
| New: Request Channel | ❌ | ✅ |
| New: Auto-Playlist | ❌ | ✅ |
