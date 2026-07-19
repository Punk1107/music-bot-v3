# 🎵 Music Bot V3

A professional, production-ready Discord music bot built in Python with a clean modular architecture, enterprise-grade stability patterns, and a polished Discord UI — all self-contained, no third-party audio services.

---

## ✨ What's New in V3

| Feature | Details |
|---------|---------|
| ❤️ **Favorites System** | Save, list, and instantly play your favorite tracks per user (up to 50) |
| 🎚️ **DJ Role** | Restrict destructive commands to a designated DJ role |
| 📻 **Request Channel** | Dedicate a text channel where typing a song name triggers playback via NLU |
| 📊 **Live Progress Bar** | Now-playing embed auto-updates every 7 seconds with a knob-style bar: `▶ ────●──── [1:23/3:31] 🔊` |
| 🔤 **Regex NLU** | EN + TH intent detection (no OpenAI/Anthropic — zero external API cost) |
| 📈 **REST API + WebSocket** | `/api/v1/` endpoints + real-time WebSocket dashboard |
| 🎼 **Auto-Playlist** | Fills queue from play history when it empties — configurable per guild |

---

## ✨ Full Feature List

| Feature | Details |
|---------|---------|
| 🎵 **YouTube Playback** | URL or search keywords; smart autocomplete from search history |
| 🎤 **Spotify Support** | Track, album, full playlist → parallel-resolved to YouTube |
| 📋 **Smart Queue** | Persistent to SQLite (write-ahead), paginated & interactive dropdown management |
| 🔁 **Loop Modes** | Off → Track → Queue, cycled via button or `/loop` command |
| 🎛 **18 Audio Effects** | Bass Boost, Nightcore, Vaporwave, Treble Boost, Vocal Boost, Karaoke, Vibrato, Tremolo, Chorus, Reverb, Echo, Distortion, Mono, Stereo Enhance, Compressor, Limiter, Noise Gate, 8D Audio |
| 🔊 **Volume Control** | 0–200% via `/volume` or ±10% buttons on now-playing embed |
| 🎮 **Interactive UI** | Dynamic skip counts, disabled states, ❤️ Favorite button on now-playing |
| 🎨 **Dynamic Colors** | Dominant accent color extracted from track thumbnails (pure Python, no Pillow) |
| 📊 **Analytics** | Per-guild play history, per-user stats, REST API analytics endpoint |
| 🛡 **Content Filter** | 7-stage pipeline — blocks NSFW/gambling/piracy (EN + TH keywords) |
| 💤 **Idle Auto-disconnect** | Configurable per-guild timeout (60–3600 seconds) |
| 🔄 **Self-healing Voice** | Exponential-backoff reconnect: 2s → 4s → 8s |
| ⚡ **Circuit Breakers** | 3-state (CLOSED / OPEN / HALF-OPEN) for YouTube and Spotify with metrics |
| ⏩ **Predictive Pre-fetch** | Next track CDN URL resolved ~15s before current track ends |
| 🔌 **FFmpeg Only** | Lavalink removed — clean, minimal, battle-tested audio pipeline |
| 🌐 **Web Dashboard** | Built-in HTML dashboard at `http://host:8080/dashboard` via WebSocket |

---

## 📁 Project Structure

```text
music-bot-v3/
├── main.py              # MusicBot class, events, 5 background tasks
├── config.py            # All settings loaded from .env
├── webserver.py         # aiohttp REST API + WebSocket dashboard
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build
├── docker-compose.yml   # Docker Compose config
│
├── cogs/
│   ├── music.py         # /join /leave /play /search /pause /resume /skip /stop /nowplaying
│   ├── queue_cog.py     # /queue /shuffle /clear /loop /remove /move
│   ├── effects.py       # /volume /effects /effects_list /effects_clear /quality
│   ├── info.py          # /history /stats /botstats /help
│   ├── favorites.py     # /favorite add/list/play/remove
│   └── admin.py         # /djset /requestchannel /autoplaylist /idletimeout
│
├── core/
│   ├── database.py      # aiosqlite — queue, history, favorites, analytics, server config
│   ├── youtube.py       # yt-dlp wrapper with stream cache, search cache, prefetch
│   ├── spotify.py       # Spotify → YouTube resolver (track / album / playlist)
│   ├── audio.py         # FFmpeg filter chain builder (18 effects)
│   ├── audio_backend.py # FFmpegBackend (play / stop abstractions)
│   ├── circuit_breaker.py # 3-state circuit breaker with failure metrics
│   ├── nlu.py           # Regex NLU engine: EN + TH, 10 intents (no LLM)
│   ├── player.py        # GuildPlayer — queue, loop, volume, progress, prefetch
│   └── validator.py     # 7-stage URL + query safety validator
│
├── models/
│   ├── track.py         # Track dataclass with serialisation + is_favorite flag
│   ├── server_config.py # Per-guild settings: DJ role, request channel, auto-playlist
│   └── enums.py         # LoopMode, AudioEffect (×18), AudioQuality, NLUIntent
│
└── utils/
    ├── embeds.py        # All Discord embed factories (now-playing, track-added, favorites, …)
    ├── views.py         # MusicControlView, QueueView, SearchSelectView, FavoritesView
    ├── color_thief.py   # Dominant color extraction (CPU-bound, runs in thread executor)
    ├── formatters.py    # format_duration, make_progress_bar, make_knob_progress_bar, …
    ├── rate_limiter.py  # Sliding-window per-guild/user rate limiter
    └── error_handler.py # Bilingual EN + TH error embeds + dev-channel forwarding
```

---

## 🖼 Discord UI Layout

### 🎵 Added to Queue

```
🎵  Added to Queue
ความรู้สึกเราไม่เท่ากัน (uneven) - FAYE PERAYA …

⏱ Duration    📋 Position    👤 Uploader
3:31           #1              marr team official

Requested by Sleepless
```

### ▶ Now Playing

```
▶  Now Playing
ความรู้สึกเราไม่เท่ากัน (uneven) - FAYE PERAYA …
↳ 👤 marr team official

▶ ──────────●────────── [0:58/3:31] 🔊

🔁 Loop    🔊 Volume    📋 Queue
Off         100%         0 tracks

👁 Views
1.3M

Requested by Sleepless
```

### 🎮 Control Buttons

**Row 0:** `⏸ Pause` · `⏭⏭ Skip` · `🔁 Loop: Off` · `✖ Shuffle` · `⏹ Stop`  
**Row 1:** `🔇 -10%` · `🔊 +10%` · `❤️ Favorite`

---

## 🚀 Quick Start

### Prerequisites
- Python **3.10+**
- [FFmpeg](https://ffmpeg.org/download.html) available on your `PATH`

### Install & Run

```bash
git clone https://github.com/Punk1107/music-bot-v3.git
cd music-bot-v3
pip install -r requirements.txt
cp .env.example .env
# Edit .env — fill in DISCORD_TOKEN and APP_ID at minimum
python main.py
```

### Docker

```bash
cp .env.example .env
# Fill in your secrets
docker-compose up -d
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | **required** | Bot token from Discord Developer Portal |
| `APP_ID` | **required** | Application ID |
| `SPOTIFY_CLIENT_ID` | *(empty)* | Spotify API key (optional; disables Spotify if absent) |
| `SPOTIFY_CLIENT_SECRET` | *(empty)* | Spotify API secret |
| `DATABASE_PATH` | `data/musicbot.db` | SQLite database file path |
| `SYNC_COMMANDS` | `false` | Sync slash commands on startup (use once after adding commands) |
| `AUTO_RESUME` | `false` | Restore queues from DB on startup |
| `IDLE_TIMEOUT` | `300` | Seconds before auto-disconnect when idle |
| `SKIP_ERROR_LIMIT` | `5` | Max consecutive broken-track auto-skips |
| `DEV_LOG_CHANNEL_ID` | *(empty)* | Channel ID for full traceback forwarding |
| `AUTO_PLAYLIST` | `false` | Fill queue from history when empty |
| `AUTO_PLAYLIST_SIZE` | `5` | Number of tracks to pull for auto-playlist |
| `MAX_FAVORITES_PER_USER` | `50` | Per-user favorites cap |
| `NLU_ENABLED` | `true` | Enable NLU intent parsing in request channels |
| `WEB_HOST` | `0.0.0.0` | Web server bind host |
| `WEB_PORT` | `8080` | Web server port |
| `API_SECRET` | *(empty)* | Bearer token for REST API (empty = no auth) |
| `API_RATE_LIMIT` | `60` | REST API requests per minute per IP |
| `YTDL_AUDIO_FORMAT` | `bestaudio[ext=webm]/bestaudio/best` | yt-dlp format selector |
| `YTDL_RETRIES` | `3` | yt-dlp retry count |
| `YTDL_TIMEOUT` | `30.0` | yt-dlp extraction timeout (seconds) |
| `YTDL_CACHE_TIMEOUT` | `1800.0` | Stream URL cache TTL (30 min) |
| `YTDL_CACHE_MAX_SIZE` | `512` | Max stream URL cache entries |
| `SEARCH_CACHE_TTL` | `600.0` | Search result cache TTL (10 min) |
| `STREAM_URL_TTL` | `14400.0` | Prefetched stream URL TTL (4 hours) |
| `EXTRACT_CONCURRENCY` | `3` | Max concurrent yt-dlp extractions |
| `MAX_TRACK_LENGTH` | `7200` | Max track duration in seconds (2 hours) |
| `MAX_PLAYLIST_TRACKS` | `100` | Max tracks imported from a playlist |
| `MAX_QUEUE_SIZE` | `500` | Max total queue size |
| `QUEUE_SAVE_INTERVAL` | `300` | Periodic queue save interval (seconds) |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | Failure count to open circuit |
| `CIRCUIT_BREAKER_WINDOW` | `60.0` | Circuit breaker recovery window (seconds) |
| `RECONNECT_ATTEMPTS` | `3` | Voice reconnect retry count |
| `RECONNECT_BASE_DELAY` | `2.0` | Base delay for exponential backoff (seconds) |
| `COLOR_EXTRACT_CONCURRENCY` | `3` | Max concurrent thumbnail color extractions |

---

## 🎛 Commands

### 🎵 Playback

| Command | Description |
|---------|-------------|
| `/join` | Join your current voice channel |
| `/leave` | Disconnect and clear the queue |
| `/play <query>` | YouTube URL, Spotify URL, playlist URL, or search terms |
| `/search <query>` | Search YouTube and choose from a dropdown of results |
| `/pause` | Pause playback |
| `/resume` | Resume paused playback |
| `/skip` | Skip the current track |
| `/stop` | Stop playback, clear queue, and disconnect |
| `/nowplaying` | Show the now-playing embed with live progress bar |

### 📋 Queue

| Command | Description |
|---------|-------------|
| `/queue [page]` | Show paginated queue with current track and progress |
| `/shuffle` | Shuffle the queue (requires ≥ 2 tracks) |
| `/clear` | Clear the entire queue |
| `/loop` | Cycle loop mode: Off → Track → Queue |
| `/remove <position>` | Remove track at the given 1-based position |
| `/move <from> <to>` | Atomically reorder a track |

### 🎛 Audio

| Command | Description |
|---------|-------------|
| `/volume <0-200>` | Set playback volume percentage |
| `/effects <name>` | Toggle one of 18 audio effects (autocomplete supported) |
| `/effects_list` | Show all 18 effects with active status |
| `/effects_clear` | Disable all active effects |
| `/quality <preset>` | Set audio quality: `low` / `medium` / `high` / `ultra` |

### ❤️ Favorites

| Command | Description |
|---------|-------------|
| `/favorite add [name]` | Save the currently playing track as a favorite |
| `/favorite list [user]` | View your (or another user's) favorites (paginated) |
| `/favorite play <name>` | Enqueue and play a saved favorite (fuzzy-match supported) |
| `/favorite remove <name>` | Delete a saved favorite |

### ⚙️ Admin (Administrator only)

| Command | Description |
|---------|-------------|
| `/djset role @role` | Set DJ role — only this role can use control commands |
| `/djset clear` | Remove DJ restriction (everyone can control) |
| `/requestchannel set #channel` | Designate a text channel for song requests |
| `/requestchannel clear` | Remove the request channel |
| `/autoplaylist on\|off` | Toggle auto-playlist for this server |
| `/idletimeout <seconds>` | Set idle auto-disconnect timer (60–3600 s) |

### 📊 Info

| Command | Description |
|---------|-------------|
| `/history [user]` | Show recent play history (last 10 tracks) |
| `/stats [user]` | Listening statistics: tracks requested + total time |
| `/botstats` | Bot performance metrics: guilds, active players, uptime, circuit state, memory |
| `/help` | Full interactive command reference |

---

## 🌐 REST API

All endpoints are served on `WEB_HOST:WEB_PORT` (default `0.0.0.0:8080`).  
Set `API_SECRET=yourtoken` in `.env` and pass `Authorization: Bearer yourtoken` to authenticate.

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Liveness check — returns `{"status":"ok"}` |
| `/ready` | GET | No | Readiness check |
| `/status` | GET | No | Full bot status JSON |
| `/dashboard` | GET | No | Live HTML dashboard (WebSocket-powered) |
| `/api/v1/guilds` | GET | Optional | Active guilds list |
| `/api/v1/guild/{id}/nowplaying` | GET | Optional | Current track + progress fraction |
| `/api/v1/guild/{id}/queue` | GET | Optional | Full queue as JSON |
| `/api/v1/guild/{id}/analytics?days=7` | GET | Optional | Play analytics for the past N days |
| `/ws/stats` | WebSocket | Optional | Real-time stats push every 5 seconds |

---

## ⚙️ Background Tasks

| Task | Interval | Purpose |
|------|----------|---------|
| `_idle_check` | 30 s | Auto-disconnect guilds idle longer than their configured timeout |
| `_queue_save` | 5 min | Periodic queue persistence to SQLite (write-ahead also triggers on enqueue) |
| `_np_refresh` | 7 s | Update now-playing embed progress bar (knob moves in real time) |
| `_cache_prune` | 30 min | Evict expired yt-dlp stream URL and search cache entries |
| `_analytics_prune` | 24 h | Prune analytics rows older than 30 days |

---

## 🔄 V2 → V3 Changes

| Component | V2 | V3 |
|-----------|----|----|
| NLU | OpenAI / Anthropic (external) | Internal Regex engine — EN + TH, 10 intents, zero API cost |
| Audio Backend | FFmpeg + Lavalink stub | FFmpeg only — clean and minimal |
| Database | aiosqlite + WAL | + favorites table, analytics, server config, immediate write-ahead saves |
| Webserver | `/health /status /ready` | + full REST API v1 + WebSocket + HTML dashboard |
| Now-playing embed | Static text progress bar | Live knob-style bar `▶ ────●──── [0:58/3:31] 🔊`, updates every 7 s |
| Now-playing embed layout | Inline status row text | Discord Fields: Loop \| Volume \| Queue \| Views (3-column cards) |
| Track-added embed | Inline description text | Discord Fields: Duration \| Position \| Uploader (3-column card) |
| Buttons | Row 0: Pause/Skip/Loop \| Row 1+: Vol/Favorite | Row 0: Pause/Skip/Loop/Shuffle/Stop \| Row 1: Vol−/Vol+/Favorite |
| Queue Save | Every 5 min | Write-ahead on enqueue + periodic every 5 min |
| Color Thief | Blocking call | Thread executor — non-blocking |
| Cache Prune | Never | Every 30 min |
| np_refresh interval | 30 s | 7 s |
| New: Favorites | ❌ | ✅ per-user, 50 cap, with fuzzy-match play |
| New: DJ Role | ❌ | ✅ per-guild, administrator-only setup |
| New: Request Channel | ❌ | ✅ NLU-powered, auto-deletes user messages |
| New: Auto-Playlist | ❌ | ✅ configurable per-guild, fills from history |

---

## 🧱 Dependencies

| Package | Purpose |
|---------|---------|
| `discord.py >= 2.6.3` | Discord gateway, slash commands, UI views |
| `PyNaCl >= 1.5.0` | Voice encryption |
| `yt-dlp >= 2026.6.9` | YouTube audio extraction and search |
| `aiohttp >= 3.14.1` | Async HTTP client (thumbnails, Spotify) + web server |
| `aiosqlite >= 0.21.0` | Async SQLite for queue, history, favorites, analytics |
| `python-dotenv >= 1.1.1` | `.env` file loading |
| `greenlet >= 3.2.3` | Async concurrency helper |

> **No Pillow, no OpenAI, no Lavalink.** All features run on the above minimal set.

---

## 📝 Logging

Three log targets configured automatically:

| Target | Level | Details |
|--------|-------|---------|
| Console (stdout) | INFO | Timestamped, human-readable |
| `logs/bot.log` | DEBUG | Rotating, 10 MB × 5 backups |
| `logs/errors.log` | ERROR | Rotating, 5 MB × 3 backups |

Noisy third-party loggers (`discord`, `aiohttp.access`) are silenced to WARNING.
