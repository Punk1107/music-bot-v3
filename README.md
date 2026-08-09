# 🎵 Music Bot V3

> **Production-ready Discord music bot** built in Python — modular architecture, enterprise-grade stability, rich Discord UI, and zero third-party audio services.

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.6.3%2B-5865F2?logo=discord)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker)](Dockerfile)

---

## ✨ Highlights

| | Feature |
|---|---|
| 🎵 | YouTube + Spotify playback — no Lavalink, no external audio servers |
| 🎛 | 18 real-time FFmpeg audio effects with stacking support |
| 🔤 | Built-in Regex NLU (EN + TH) — zero OpenAI / Anthropic cost |
| 📊 | Live progress bar, REST API v1, WebSocket dashboard |
| 🛡 | Circuit breakers, self-healing voice, dead-task watchdog |
| 💾 | Full session state persistence — resume after crash or restart |
| 🌐 | Bilingual UI (English / Thai) switchable per guild |
| 🎨 | 4 embed themes + dynamic thumbnail color extraction |

---

## 📁 Project Structure

```text
music-bot-v3/
├── main.py              # MusicBot class — events, 10 background tasks, NLU request channel
├── config.py            # All settings loaded from .env with type validation
├── webserver.py         # aiohttp REST API v1 + WebSocket + HTML dashboard
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build (Python 3.12-slim + FFmpeg)
├── docker-compose.yml   # Docker Compose with healthcheck
├── render.yaml          # Render.com one-click deploy config
│
├── cogs/                # Discord slash-command cogs (14 cogs)
│   ├── music.py         # /join /leave /play /search /pause /resume /skip /stop /nowplaying
│   ├── queue_cog.py     # /queue /shuffle /clear /loop /remove /move
│   ├── effects.py       # /volume /effects /effects_list /effects_clear /quality
│   ├── playback_cog.py  # /speed /pitch /crossfade /silencetrim /replaygain
│   ├── favorites.py     # /favorite add/list/play/remove
│   ├── bookmark_cog.py  # /bookmark save/load/list/delete — queue snapshots
│   ├── presets_cog.py   # /preset load/save/list/delete — bundled effect presets
│   ├── theme_cog.py     # /theme /themeinfo — embed visual themes
│   ├── sleep_timer_cog.py # /sleep /sleepstatus — auto-stop timer
│   ├── language_cog.py  # /language — per-guild EN/TH switching
│   ├── analytics_cog.py # /analytics heatmap/genre/peak/top/streak
│   ├── health_cog.py    # /health — full system health report
│   ├── info.py          # /history /stats /botstats /help
│   └── admin.py         # /djset /requestchannel /autoplaylist /idletimeout
│
├── core/                # Internal services (18 modules)
│   ├── database.py      # aiosqlite — queue, history, favorites, bookmarks, analytics, presets
│   ├── youtube.py       # yt-dlp wrapper — stream cache, search cache, predictive prefetch
│   ├── spotify.py       # Spotify → YouTube resolver (track / album / playlist)
│   ├── audio.py         # FFmpeg filter chain builder (18 effects + speed/pitch/crossfade)
│   ├── audio_backend.py # FFmpegBackend (play / stop abstractions)
│   ├── player.py        # GuildPlayer — queue, loop, volume, progress, prefetch
│   ├── circuit_breaker.py # 3-state circuit breaker (CLOSED/OPEN/HALF-OPEN)
│   ├── nlu.py           # Regex NLU engine — EN + TH, 10 intents, no LLM
│   ├── validator.py     # 7-stage URL + query safety validator
│   ├── i18n.py          # Localization — EN/TH string catalogue with fallback
│   ├── lru_cache.py     # LRU caches for metadata / thumbnails / search results
│   ├── media_cache.py   # Thumbnail prewarm + background metadata refresh
│   ├── ffmpeg_pool.py   # FFmpeg warm pool for instant process startup
│   ├── metrics.py       # Runtime metrics snapshots (every 5 min)
│   ├── stability.py     # ExceptionKind taxonomy, DeadTaskWatchdog, MemoryLeakDetector
│   ├── self_test.py     # Startup self-test suite (F34)
│   └── startup_validator.py # Pre-login env validation (fatal errors exit before connect)
│
├── models/              # Data models
│   ├── track.py         # Track dataclass — serialisation + is_favorite flag
│   ├── server_config.py # Per-guild settings (DJ role, request channel, language, theme…)
│   └── enums.py         # LoopMode, AudioEffect ×18, AudioQuality, NLUIntent ×10,
│                        # EmbedTheme ×4, DuplicateMode, QueuePermission
│
└── utils/               # Shared utilities
    ├── embeds.py        # All Discord embed factories
    ├── views.py         # MusicControlView, QueueView, SearchSelectView, FavoritesView…
    ├── color_thief.py   # Dominant color extraction from thumbnails (thread executor)
    ├── formatters.py    # format_duration, make_progress_bar, make_knob_progress_bar…
    ├── rate_limiter.py  # Sliding-window per-guild/user rate limiter
    └── error_handler.py # Bilingual EN+TH error embeds + dev-channel forwarding
```

---

## 🎛 Full Feature List

### Core Playback
| Feature | Details |
|---------|---------|
| 🎵 **YouTube Playback** | URL or search keywords; smart autocomplete from search history |
| 🎤 **Spotify Support** | Track, album, full playlist → resolved to YouTube in parallel |
| 📋 **Smart Queue** | Persistent to SQLite (WAL), paginated & interactive dropdown management |
| 🔁 **Loop Modes** | Off → Track → Queue, cycled via button or `/loop` |
| 🔊 **Volume Control** | 0–200% via `/volume` or ±10% buttons on now-playing embed |
| ⏩ **Predictive Pre-fetch** | Next track's CDN URL resolved ~15 s before current track ends |

### Audio Effects
| Feature | Details |
|---------|---------|
| 🎚 **18 Audio Effects** | Bass Boost, Nightcore, Vaporwave, Treble Boost, Vocal Boost, Karaoke, Vibrato, Tremolo, Chorus, Reverb, Echo, Distortion, Mono, Stereo Enhance, Compressor, Limiter, Noise Gate, 8D Audio |
| ⚡ **Speed Control** | 0.75× – 2.0× via FFmpeg `atempo` (pitch unchanged) |
| 🎼 **Pitch Shift** | −2 to +2 semitones via FFmpeg |
| 🎵 **Crossfade** | Smooth transition between tracks (off / 3 s / 5 s / 8 s) |
| ✂️ **Silence Trim** | Remove leading/trailing silence from each track |
| 📢 **Replay Gain** | Loudness normalization across tracks |
| 📦 **Guild Presets** | Save/load bundles: effects + volume + quality + speed + pitch + crossfade |
| 🎮 **Built-in Presets** | Gaming, Study, Anime, Chill — apply with one command |

### User Features
| Feature | Details |
|---------|---------|
| ❤️ **Favorites System** | Save, list, and instantly play favorite tracks per user (up to 50) |
| 🔖 **Queue Bookmarks** | Snapshot the entire queue — save, restore, list, delete |
| 💤 **Sleep Timer** | Auto-stop playback after a duration (`20m`, `1h30m`, `off`) |
| 📊 **Analytics Dashboard** | Heatmap, genre distribution, peak hours, top artists, listening streaks |

### Server Management
| Feature | Details |
|---------|---------|
| 🎚️ **DJ Role** | Restrict destructive commands to a designated DJ role |
| 📻 **Request Channel** | Dedicate a text channel — typing a song name triggers NLU + playback |
| 🎼 **Auto-Playlist** | Fills queue from play history when it empties (configurable per guild) |
| 💤 **Idle Auto-disconnect** | Configurable per-guild timeout (60–3600 s) |
| 🌐 **Bilingual UI** | Switch between English and Thai per guild with `/language` |
| 🎨 **Embed Themes** | Classic, Spotify, Minimal, Glass — switchable per guild |

### Stability & Performance
| Feature | Details |
|---------|---------|
| ⚡ **Circuit Breakers** | 3-state (CLOSED/OPEN/HALF-OPEN) for YouTube + Spotify with failure metrics |
| 🔄 **Self-healing Voice** | Exponential-backoff reconnect: 2 s → 4 s → 8 s |
| 🧠 **Dead Task Watchdog** | Detects and restarts crashed background loops every 60 s |
| 📉 **Memory Leak Detector** | RSS + thread + cache snapshots every 30 min; warns on anomalous growth |
| 🗜️ **Memory Pressure Handler** | Evicts LRU caches automatically when bot process RSS exceeds threshold |
| 📦 **FFmpeg Warm Pool** | Pre-warmed FFmpeg processes for instant audio startup |
| 💾 **Full Session State** | Queue + loop + volume + effects + position saved every 60 s; restorable on restart |
| 🔌 **Pre-login Validator** | Checks required env vars before connecting — exits early on fatal errors |
| 🧪 **Startup Self-test** | Non-blocking test suite on bot ready; results visible in `/health` |
| 🛡 **Content Filter** | 7-stage pipeline blocking NSFW/gambling/piracy (EN + TH keywords) |
| 🔤 **Regex NLU** | EN + TH intent detection — 10 intents, zero API cost |

---

## 🖼 Discord UI Layout

### ▶️ Now Playing

```
🎵  Awesome Song Title — Example Artist Official MV    [thumbnail]
🔵  Example Channel

⏱ Duration   👁 Views    📋 In Queue
3:31          1.3M        0 tracks

👤 Requested by    🔁 Loop         🔊 Volume
@Username          🔁 Loop: Off    100%

▶️ Progress
▶️ [──────●](url)────────────── [0:58/3:31] 🔉

──────────────────────────────────────────────
[bot avatar]  Music Bot V3  •  Now Playing
```

> **Two-tone progress bar** (width=32): The filled segment and knob `●` are wrapped in a Markdown hyperlink so Discord renders them in accent/link colour. The remaining `─` characters stay grey — giving a Spotify-like two-tone look. Refreshes every **7 seconds**.

### 🎵 Added to Queue

```
🎵  Added to Queue
Awesome Song Title - Example Artist Official MV …    [thumbnail]

⏱ Duration    📋 Position    👤 Uploader
3:31           #3              Example Channel

Requested by Username
```

### 🎮 Control Buttons

**Row 0:** `⏸ Pause` · `⏭ Skip (n)` · `🔁 Loop: Off` · `🔀 Shuffle` · `⏹ Stop`  
**Row 1:** `🔇 −10%` · `🔊 +10%` · `❤️ Favorite`

**Loop button** cycles and shows current state:
- `🔁 Loop: Off` (grey) → `🔂 Loop: Track` (blue) → `🔁 Loop: Queue` (blue)

> **Note:** `/stop` stops playback and clears the queue but the bot **stays in the voice channel**. Use `/leave` to disconnect.

---

## 🚀 Quick Start

### Prerequisites

- Python **3.12+**
- [FFmpeg](https://ffmpeg.org/download.html) on your system `PATH`

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
# Edit .env — fill in your secrets
docker-compose up -d
```

### Deploy to Render.com

The included [`render.yaml`](render.yaml) enables one-click deploy as a Docker web service with auto-deploy on push. Set your environment variables in the Render dashboard.

---

## ⚙️ Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `APP_ID` | Application ID |

### Optional — Bot Behaviour

| Variable | Default | Description |
|----------|---------|-------------|
| `SPOTIFY_CLIENT_ID` | *(empty)* | Spotify API key (disables Spotify if absent) |
| `SPOTIFY_CLIENT_SECRET` | *(empty)* | Spotify API secret |
| `DATABASE_PATH` | `data/musicbot.db` | SQLite database file path |
| `SYNC_COMMANDS` | `false` | Sync slash commands on startup (use once after changes) |
| `AUTO_RESUME` | `false` | Restore full session state from DB on startup |
| `IDLE_TIMEOUT` | `300` | Seconds before auto-disconnect when idle |
| `SKIP_ERROR_LIMIT` | `5` | Max consecutive broken-track auto-skips |
| `DEV_LOG_CHANNEL_IDS` | *(empty)* | Comma-separated channel IDs for traceback forwarding |
| `AUTO_PLAYLIST` | `false` | Fill queue from history when empty |
| `AUTO_PLAYLIST_SIZE` | `5` | Number of tracks to pull for auto-playlist |
| `MAX_FAVORITES_PER_USER` | `50` | Per-user favorites cap |
| `NLU_ENABLED` | `true` | Enable NLU intent parsing in request channels |

### Optional — Web Server

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_HOST` | `0.0.0.0` | Web server bind host |
| `WEB_PORT` | `8080` | Web server port |
| `API_SECRET` | *(empty)* | Bearer token for REST API (empty = no auth) |
| `API_RATE_LIMIT` | `60` | REST API requests per minute per IP |

### Optional — Audio & Performance

| Variable | Default | Description |
|----------|---------|-------------|
| `YTDL_AUDIO_FORMAT` | `bestaudio[ext=webm]/bestaudio/best` | yt-dlp format selector |
| `YTDL_RETRIES` | `3` | yt-dlp retry count |
| `YTDL_TIMEOUT` | `30.0` | yt-dlp extraction timeout (seconds) |
| `YTDL_STREAM_TIMEOUT` | `20.0` | yt-dlp stream connection timeout (seconds) |
| `YTDL_CACHE_TIMEOUT` | `1800.0` | Stream URL cache TTL (30 min) |
| `YTDL_CACHE_MAX_SIZE` | `512` | Max stream URL cache entries |
| `SEARCH_CACHE_TTL` | `600.0` | Search result cache TTL (10 min) |
| `SEARCH_CACHE_MAX_SIZE` | `256` | Max search cache entries |
| `STREAM_URL_TTL` | `14400.0` | Prefetched stream URL TTL (4 hours) |
| `EXTRACT_CONCURRENCY` | `3` | Max concurrent yt-dlp extractions |
| `PLAYLIST_EXTRACT_CONCURRENCY` | `2` | Max concurrent playlist extraction jobs |
| `COLOR_EXTRACT_CONCURRENCY` | `3` | Max concurrent thumbnail color extractions |
| `MAX_TRACK_LENGTH` | `7200` | Max track duration in seconds (2 hours) |
| `MAX_PLAYLIST_TRACKS` | `100` | Max tracks imported from a playlist |
| `MAX_QUEUE_SIZE` | `500` | Max total queue size |
| `QUEUE_SAVE_INTERVAL` | `300` | Periodic queue save interval (seconds) |

### Optional — Stability

| Variable | Default | Description |
|----------|---------|-------------|
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | Failure count to open a circuit |
| `CIRCUIT_BREAKER_WINDOW` | `60.0` | Circuit breaker recovery window (seconds) |
| `RECONNECT_ATTEMPTS` | `3` | Voice reconnect retry count |
| `RECONNECT_BASE_DELAY` | `2.0` | Base delay for exponential backoff (seconds) |

---

## 🎛 Commands

### 🎵 Playback

| Command | Description |
|---------|-------------|
| `/join` | Join your current voice channel |
| `/leave` | Disconnect the bot and clear the queue |
| `/play <query>` | YouTube URL, Spotify URL, playlist URL, or search terms |
| `/search <query>` | Search YouTube and choose from a dropdown of results |
| `/pause` | Pause playback |
| `/resume` | Resume paused playback |
| `/skip` | Skip the current track |
| `/stop` | Stop playback and clear queue — bot stays in voice channel |
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

### 🎚 Audio Effects

| Command | Description |
|---------|-------------|
| `/volume <0-200>` | Set playback volume percentage |
| `/effects <name>` | Toggle one of 18 audio effects (autocomplete supported) |
| `/effects_list` | Show all 18 effects with active status |
| `/effects_clear` | Disable all active effects |
| `/quality <preset>` | Set audio quality: `low` / `medium` / `high` / `ultra` |

### ⚡ Advanced Playback

| Command | Description |
|---------|-------------|
| `/speed <rate>` | Playback speed: 0.75× / 1.0× / 1.25× / 1.5× / 2.0× (no pitch change) |
| `/pitch <semitones>` | Pitch shift: −2 / −1 / 0 / +1 / +2 semitones |
| `/crossfade <duration>` | Crossfade between tracks: off / 3 s / 5 s / 8 s |
| `/silencetrim` | Toggle silence trimming at track start and end |
| `/replaygain` | Toggle loudness normalization across tracks |

### ❤️ Favorites

| Command | Description |
|---------|-------------|
| `/favorite add [name]` | Save the currently playing track as a favorite |
| `/favorite list [user]` | View your (or another user's) favorites (paginated) |
| `/favorite play <name>` | Enqueue and play a saved favorite (fuzzy-match supported) |
| `/favorite remove <name>` | Delete a saved favorite |

### 🔖 Queue Bookmarks

| Command | Description |
|---------|-------------|
| `/bookmark save <name>` | Snapshot the current queue as a named bookmark |
| `/bookmark load <name>` | Restore a bookmark (append or replace) |
| `/bookmark list` | Show all your bookmarks for this server |
| `/bookmark delete <name>` | Remove a bookmark |

### 📦 Presets

| Command | Description |
|---------|-------------|
| `/preset load <name>` | Apply a built-in or saved preset (Gaming, Study, Anime, Chill, or custom) |
| `/preset save <name>` | Save current effects/volume/quality/speed/pitch as a named preset |
| `/preset list` | Show all available presets |
| `/preset delete <name>` | Delete a custom preset (Admin only) |

### 🎨 Themes

| Command | Description |
|---------|-------------|
| `/theme <name>` | Set embed theme for this guild: `classic` / `spotify` / `minimal` / `glass` |
| `/themeinfo` | Preview all 4 available themes |

### 💤 Sleep Timer

| Command | Description |
|---------|-------------|
| `/sleep <duration>` | Set a sleep timer (`20m`, `1h`, `1h30m`, `off`) |
| `/sleepstatus` | Show time remaining on the active sleep timer |

### 📊 Analytics

| Command | Description |
|---------|-------------|
| `/analytics heatmap` | Day-of-week × hour heatmap of listening activity |
| `/analytics genre` | Genre distribution inferred from track metadata |
| `/analytics peak` | Top peak listening hours with bar chart |
| `/analytics top` | Top Artists / Top Channels leaderboard |
| `/analytics streak` | Listening streak tracker (7 / 15 / 30 days) |

### 📈 Info & Stats

| Command | Description |
|---------|-------------|
| `/history [user]` | Show recent play history (last 10 tracks) |
| `/stats [user]` | Listening statistics: tracks requested + total time |
| `/botstats` | Bot metrics: guilds, active players, uptime, circuit state, memory |
| `/health` | Full system health report (Admin/DJ) — caches, memory, self-test, circuit breakers |
| `/help` | Full interactive command reference |

### 🌐 Language

| Command | Description |
|---------|-------------|
| `/language <en\|th>` | Set the UI language for this server (English or Thai) |

### ⚙️ Admin (Administrator only)

| Command | Description |
|---------|-------------|
| `/djset role @role` | Set DJ role — only this role can use control commands |
| `/djset clear` | Remove DJ restriction (everyone can control) |
| `/requestchannel set #channel` | Designate a text channel for NLU song requests |
| `/requestchannel clear` | Remove the request channel |
| `/autoplaylist on\|off` | Toggle auto-playlist for this server |
| `/idletimeout <seconds>` | Set idle auto-disconnect timer (60–3600 s) |

---

## 🌐 REST API

All endpoints served on `WEB_HOST:WEB_PORT` (default `0.0.0.0:8080`).  
Set `API_SECRET=yourtoken` in `.env` and pass `Authorization: Bearer yourtoken` to authenticate.

Generate a token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Liveness check — `{"status":"ok"}` |
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
| `_idle_check` | 30 s | Auto-disconnect idle guilds; prune stale GuildPlayer entries |
| `_queue_save` | 5 min | Periodic queue persistence to SQLite |
| `_np_refresh` | 7 s | Update now-playing embed progress bar |
| `_cache_prune` | 30 min | Evict expired yt-dlp stream / search / media cache entries |
| `_analytics_prune` | 24 h | Prune analytics rows older than 30 days |
| `_session_heartbeat` | 60 s | Save full playback state (crash loses ≤ 60 s of position) |
| `_memory_pressure` | 60 s | Evict LRU caches when bot process RSS exceeds threshold |
| `_metrics_snapshot` | 5 min | Collect and log runtime metrics |
| `_memory_leak_detect` | 30 min | RSS/thread/cache snapshots; warn on anomalous growth |
| `_dead_task_watchdog` | 60 s | Detect and restart crashed background loops |

---

## 🔤 NLU Intents (Request Channel)

The built-in Regex NLU engine supports **10 intents** in both English and Thai:

| Intent | EN Triggers | TH Triggers |
|--------|------------|------------|
| `PLAY` | `play <query>` | `เล่น`, `เปิดเพลง`, `ขอฟัง` |
| `PAUSE` | `pause`, `stop playing` | `หยุด`, `พัก` |
| `RESUME` | `resume`, `unpause` | `เล่นต่อ`, `ต่อ` |
| `SKIP` | `skip`, `next` | `ข้าม`, `ต่อไป`, `เพลงต่อไป` |
| `STOP` | `stop`, `disconnect` | `หยุดเลย`, `ออก`, `ปิดบอท` |
| `VOLUME` | `volume`, `louder`, `quieter` | `เสียง`, `ดังขึ้น`, `เบาลง` |
| `QUEUE` | `queue`, `list` | `คิว`, `รายการเพลง` |
| `LOOP` | `loop`, `repeat` | `วนซ้ำ`, `ซ้ำ` |
| `SHUFFLE` | `shuffle`, `random` | `สุ่ม`, `สุ่มเพลง` |
| `UNKNOWN` | *(fallback — treated as a search query if it looks like a song name)* | |

---

## 📝 Logging

Three log targets configured automatically:

| Target | Level | Details |
|--------|-------|---------|
| Console (stdout) | INFO | Timestamped, human-readable |
| `logs/bot.log` | DEBUG | Rotating, 10 MB × 5 backups |
| `logs/errors.log` | ERROR | Rotating, 5 MB × 3 backups |

Noisy third-party loggers (`discord`, `discord.http`, `discord.gateway`, `aiohttp.access`) are silenced to Warning.

---

## 🧱 Dependencies

| Package | Purpose |
|---------|---------|
| `discord.py >= 2.6.3` | Discord gateway, slash commands, UI views |
| `PyNaCl >= 1.5.0` | Voice encryption |
| `yt-dlp >= 2026.6.9` | YouTube audio extraction and search |
| `aiohttp >= 3.14.1` | Async HTTP client + REST/WebSocket web server |
| `aiohappyeyeballs >= 2.7.1` | Happy Eyeballs async connection algorithm (aiohttp dep) |
| `yarl >= 1.24.2` | URL parsing (aiohttp dep) |
| `idna >= 3.18` | Internationalized domain names (aiohttp dep) |
| `aiosqlite >= 0.21.0` | Async SQLite for queue, history, favorites, analytics |
| `python-dotenv >= 1.1.1` | `.env` file loading |
| `greenlet >= 3.2.3` | Async concurrency helper |
| `psutil` *(optional)* | Memory usage in `/botstats` and `/health` — skipped gracefully if absent |

> **No Pillow, no OpenAI, no Lavalink.** All core features run on the minimal set above.

---

## 🔄 V2 → V3 Changes

| Component | V2 | V3 |
|-----------|----|----|
| NLU | OpenAI / Anthropic (external) | Internal Regex engine — EN + TH, 10 intents, zero API cost |
| Audio Backend | FFmpeg + Lavalink stub | FFmpeg only — clean and minimal |
| Database | aiosqlite + WAL | + favorites, bookmarks, analytics, server config, preset, session state |
| Webserver | `/health /status /ready` | + full REST API v1 + WebSocket + HTML dashboard |
| Now-playing layout | Single description block | Field layout: Title → Uploader → Duration/Views/Queue → Req by/Loop/Volume → Progress |
| Now-playing progress bar | Plain text bar | Two-tone knob bar (width=32), updates every 7 s |
| Loop button | `🔁 Loop` (no state shown) | Shows state: `🔁 Loop: Off` / `🔂 Loop: Track` / `🔁 Loop: Queue` |
| `/stop` behavior | Stop + disconnect bot | Stop + clear queue, **bot stays in voice channel** (`/leave` to disconnect) |
| Track-added embed | Inline description | Discord Fields: Duration \| Position \| Uploader (3-column card) |
| Buttons | Row 0: Pause/Skip/Loop | Row 0: Pause/Skip/Loop/Shuffle/Stop · Row 1: Vol−/Vol+/Favorite |
| Queue save | Every 5 min | Write-ahead on enqueue + periodic every 5 min |
| Color Thief | Blocking call | Thread executor — non-blocking |
| Cache prune | Never | Every 30 min (yt-dlp + media caches) |
| np_refresh interval | 30 s | 7 s |
| Session persistence | Queue only | Full state: queue + loop + volume + effects + position, heartbeat every 60 s |
| Background tasks | 5 tasks | 10 tasks (+ session heartbeat, memory pressure, metrics, leak detect, watchdog) |
| New: Favorites | ❌ | ✅ per-user, 50 cap, fuzzy-match play |
| New: Queue Bookmarks | ❌ | ✅ per-user queue snapshots |
| New: Sleep Timer | ❌ | ✅ human duration strings (`20m`, `1h30m`) |
| New: Advanced Playback | ❌ | ✅ speed / pitch / crossfade / silence trim / replay gain |
| New: Guild Presets | ❌ | ✅ built-in + custom, save/load atomically |
| New: Embed Themes | ❌ | ✅ Classic / Spotify / Minimal / Glass |
| New: Analytics Dashboard | ❌ | ✅ heatmap / genre / peak / top / streak |
| New: Localization | ❌ | ✅ EN + TH per-guild, full string catalogue |
| New: Health Report | ❌ | ✅ caches, memory, circuit breakers, self-test |
| New: DJ Role | ❌ | ✅ per-guild, administrator-only setup |
| New: Request Channel | ❌ | ✅ NLU-powered, auto-deletes user messages |
| New: Auto-Playlist | ❌ | ✅ configurable per-guild, fills from history |
| New: Circuit Breakers | ❌ | ✅ YouTube + Spotify, 3-state with metrics |
| New: Dead Task Watchdog | ❌ | ✅ detects and restarts crashed loops |
| New: Memory Leak Detector | ❌ | ✅ RSS snapshots every 30 min |
| New: Startup Self-test | ❌ | ✅ non-blocking suite, visible in `/health` |

---

## ✨ What's New in V3

| Feature | Details |
|---------|---------|
| ❤️ **Favorites System** | Save, list, and instantly play your favorite tracks per user (up to 50) |
| 🎚️ **DJ Role** | Restrict destructive commands to a designated DJ role |
| 📻 **Request Channel** | Dedicate a text channel where typing a song name triggers playback via NLU |
| 📊 **Live Progress Bar** | Now-playing embed auto-updates every 7 seconds with a two-tone knob-style bar: `▶️ [──────────────●](url)───── [1:23/3:31] 🔉` (width=32) |
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
| 🎮 **Interactive UI** | Dynamic skip counts, disabled states, ❤️ Favorite button on now-playing, Loop button shows current state |
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
├── Dockerfile           # Container build (Python 3.12-slim)
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
│   └── enums.py         # LoopMode, AudioEffect (×18), AudioQuality, NLUIntent (×10)
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
Awesome Song Title - Example Artist Official MV …        [thumbnail]

⏱ Duration    📋 Position    👤 Uploader
3:31           #1              Example Channel

Requested by Username
```

### ▶️ Now Playing

```
🎵  Awesome Song Title - Example Artist Official MV …   [thumbnail]
🔵  Example Channel

⏱ Duration    👁 Views    📋 In Queue
3:31           1.3M        0 tracks

👤 Requested by    🔁 Loop    🔊 Volume
@Username          Off        100%

▶️ Progress
▶️ [──────●](url)────────────── [0:58/3:31] 🔉

─────────────────────────────────────────────
[bot avatar]  Music Bot V3  •  Now Playing
```

> **Two-tone bar** (width=32): The filled segment and knob `●` are wrapped in a Markdown hyperlink `[────●](url)`, so Discord renders them in the accent/link colour. The remaining `─` characters stay grey — giving a Spotify-like two-tone look. The bar refreshes automatically every 7 seconds.

### 🎮 Control Buttons

**Row 0:** `⏸ Pause` · `⏭ Skip (n)` · `🔁 Loop: Off` · `🔀 Shuffle` · `⏹ Stop`
**Row 1:** `🔇 -10%` · `🔊 +10%` · `❤️ Favorite`

**Loop button** cycles and shows its current state:
- `🔁 Loop: Off` (grey) → `🔂 Loop: Track` (blue) → `🔁 Loop: Queue` (blue)

**Stop button** stops playback and clears the queue — **bot stays in the voice channel**.  
Use `/leave` to disconnect the bot.

---

## 🚀 Quick Start

### Prerequisites
- Python **3.12+**
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
| `YTDL_STREAM_TIMEOUT` | `20.0` | yt-dlp stream connection timeout (seconds) |
| `YTDL_CACHE_TIMEOUT` | `1800.0` | Stream URL cache TTL (30 min) |
| `YTDL_CACHE_MAX_SIZE` | `512` | Max stream URL cache entries |
| `SEARCH_CACHE_TTL` | `600.0` | Search result cache TTL (10 min) |
| `SEARCH_CACHE_MAX_SIZE` | `256` | Max search cache entries |
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
| `/leave` | Disconnect the bot and clear the queue |
| `/play <query>` | YouTube URL, Spotify URL, playlist URL, or search terms |
| `/search <query>` | Search YouTube and choose from a dropdown of results |
| `/pause` | Pause playback |
| `/resume` | Resume paused playback |
| `/skip` | Skip the current track |
| `/stop` | Stop playback and clear queue — **bot stays in the voice channel** |
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
| Now-playing layout | Single description block | Gen-2 field layout: Title → Uploader → Duration/Views/Queue → Requested by/Loop/Volume → Progress bar |
| Now-playing progress bar | Plain text bar | Two-tone knob bar (width=32) `▶️ [──────────────●](url)─── [0:58/3:31] 🔉`, updates every 7 s |
| Loop button | `🔁 Loop` (no state shown) | Shows current state: `🔁 Loop: Off` / `🔂 Loop: Track` / `🔁 Loop: Queue` |
| `/stop` behavior | Stop + disconnect bot | Stop + clear queue, **bot stays in voice channel** (`/leave` to disconnect) |
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
| `aiohappyeyeballs >= 2.7.1` | Happy Eyeballs async connection algorithm (aiohttp dep) |
| `yarl >= 1.24.2` | URL parsing (aiohttp dep) |
| `idna >= 3.18` | Internationalized domain names (aiohttp dep) |
| `aiosqlite >= 0.21.0` | Async SQLite for queue, history, favorites, analytics |
| `python-dotenv >= 1.1.1` | `.env` file loading |
| `greenlet >= 3.2.3` | Async concurrency helper |
| `psutil` *(optional)* | Memory usage in `/botstats` — gracefully skipped if absent |

> **No Pillow, no OpenAI, no Lavalink.** All core features run on the above minimal set.

---

## 📝 Logging

Three log targets configured automatically:

| Target | Level | Details |
|--------|-------|---------|
| Console (stdout) | INFO | Timestamped, human-readable |
| `logs/bot.log` | DEBUG | Rotating, 10 MB × 5 backups |
| `logs/errors.log` | ERROR | Rotating, 5 MB × 3 backups |

Noisy third-party loggers (`discord`, `discord.http`, `discord.gateway`, `aiohttp.access`) are silenced to Warning.

---

## 🔤 NLU Intents (Request Channel)

The built-in Regex NLU engine supports **10 intents** in both English and Thai:

| Intent | EN Triggers | TH Triggers |
|--------|------------|------------|
| `PLAY` | `play <query>` | `เล่น`, `เปิดเพลง`, `ขอฟัง` |
| `PAUSE` | `pause`, `stop playing` | `หยุด`, `พัก` |
| `RESUME` | `resume`, `unpause` | `เล่นต่อ`, `ต่อ` |
| `SKIP` | `skip`, `next` | `ข้าม`, `ต่อไป`, `เพลงต่อไป` |
| `STOP` | `stop`, `disconnect` | `หยุดเลย`, `ออก`, `ปิดบอท` |
| `VOLUME` | `volume`, `louder`, `quieter` | `เสียง`, `ดังขึ้น`, `เบาลง` |
| `QUEUE` | `queue`, `list` | `คิว`, `รายการเพลง` |
| `LOOP` | `loop`, `repeat` | `วนซ้ำ`, `ซ้ำ` |
| `SHUFFLE` | `shuffle`, `random` | `สุ่ม`, `สุ่มเพลง` |
| `UNKNOWN` | *(fallback — treated as search query if it looks like a song name)* | |
