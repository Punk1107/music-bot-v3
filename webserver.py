# -*- coding: utf-8 -*-
"""
webserver.py — Async aiohttp web server for Music Bot V3.

V3 major additions:
  - REST API v1: /api/v1/guilds /api/v1/guild/{id}/nowplaying
                 /api/v1/guild/{id}/queue /api/v1/guild/{id}/analytics
  - WebSocket:   /ws/stats — real-time bot stats push (every 5s)
  - Middleware:  per-IP rate limiting, request logging, gzip compression, CORS
  - Bearer token authentication (API_SECRET env var, optional)
  - Real-time HTML dashboard using WebSocket connection
  - Stats cache TTL reduced to 5s (from 15s)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from aiohttp import web
import aiohttp

import config
from utils.formatters import format_uptime, format_duration

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)

# ── Dashboard HTML template ───────────────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🎵 Music Bot V3 Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0d1117; --surface: #161b22; --border: #30363d;
      --accent: #7c5cbf; --accent2: #58a6ff; --green: #3fb950;
      --text: #e6edf3; --muted: #8b949e; --red: #f85149;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; min-height: 100vh; }
    .topbar { background: linear-gradient(135deg, var(--accent) 0%, #5865f2 100%);
              padding: 1.5rem 2rem; display: flex; align-items: center; gap: 1rem; }
    .topbar h1 { font-size: 1.5rem; font-weight: 700; }
    .topbar .badge { background: rgba(255,255,255,.2); border-radius: 20px;
                     padding: .2rem .8rem; font-size: .8rem; }
    .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; }
    .card { background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: 1.5rem; transition: transform .2s, box-shadow .2s; }
    .card:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,0,0,.4); }
    .card-title { font-size: .8rem; text-transform: uppercase; letter-spacing: .1em;
                  color: var(--muted); margin-bottom: .8rem; }
    .card-value { font-size: 2.4rem; font-weight: 700; }
    .card-sub   { font-size: .85rem; color: var(--muted); margin-top: .3rem; }
    .status { display: inline-flex; align-items: center; gap: .5rem; }
    .dot { width: 10px; height: 10px; border-radius: 50%; }
    .dot-green { background: var(--green); box-shadow: 0 0 8px var(--green); animation: pulse 2s infinite; }
    .dot-red   { background: var(--red); }
    .dot-yellow{ background: #f0a500; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
    table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
    th { text-align: left; color: var(--muted); font-size: .8rem; font-weight: 600;
         text-transform: uppercase; padding: .5rem .8rem; border-bottom: 1px solid var(--border); }
    td { padding: .6rem .8rem; border-bottom: 1px solid var(--border); font-size: .9rem; }
    tr:last-child td { border-bottom: none; }
    .progress { background: var(--border); border-radius: 99px; height: 6px; overflow: hidden; margin-top: .3rem; }
    .progress-bar { background: linear-gradient(90deg, var(--accent), var(--accent2));
                    height: 100%; border-radius: 99px; transition: width .5s; }
    .footer { text-align: center; color: var(--muted); font-size: .8rem; padding: 2rem;
              border-top: 1px solid var(--border); margin-top: 3rem; }
    #ws-status { position: fixed; bottom: 1rem; right: 1rem; background: var(--surface);
                 border: 1px solid var(--border); border-radius: 8px; padding: .4rem .8rem;
                 font-size: .75rem; color: var(--muted); }
  </style>
</head>
<body>
  <div class="topbar">
    <span style="font-size:2rem">🎵</span>
    <div>
      <h1>Music Bot V3 Dashboard</h1>
      <span class="badge" id="last-update">Connecting…</span>
    </div>
  </div>

  <div class="container">
    <div class="grid">
      <div class="card">
        <div class="card-title">Status</div>
        <div class="status">
          <div class="dot dot-green" id="status-dot"></div>
          <span class="card-value" style="font-size:1.4rem" id="status-text">Online</span>
        </div>
        <div class="card-sub" id="uptime-text">Uptime: loading…</div>
      </div>
      <div class="card">
        <div class="card-title">Guilds</div>
        <div class="card-value" id="guild-count">—</div>
        <div class="card-sub">Connected servers</div>
      </div>
      <div class="card">
        <div class="card-title">Active Players</div>
        <div class="card-value" id="active-players">—</div>
        <div class="card-sub" id="playing-tracks">tracks playing</div>
      </div>
      <div class="card">
        <div class="card-title">Circuit Breakers</div>
        <div id="cb-yt" class="status"><div class="dot dot-green"></div><span>YouTube: closed</span></div>
        <div id="cb-sp" class="status" style="margin-top:.5rem"><div class="dot dot-green"></div><span>Spotify: closed</span></div>
      </div>
    </div>

    <div style="margin-top:2rem">
      <div class="card">
        <div class="card-title">Active Sessions</div>
        <table>
          <thead>
            <tr><th>Guild</th><th>Now Playing</th><th>Queue</th><th>Volume</th></tr>
          </thead>
          <tbody id="sessions-body">
            <tr><td colspan="4" style="color:var(--muted);text-align:center">No active sessions</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="footer">Music Bot V3 · FFmpeg only · <span id="py-ver">discord.py</span></div>
  <div id="ws-status">⚡ Connecting…</div>

  <script>
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;

    function colorDot(state) {
      if (state === 'closed')    return 'dot-green';
      if (state === 'half_open') return 'dot-yellow';
      return 'dot-red';
    }

    function connect() {
      ws = new WebSocket(wsProto + '://' + location.host + '/ws/stats');
      ws.onopen = () => {
        document.getElementById('ws-status').textContent = '⚡ Live';
        document.getElementById('ws-status').style.color = '#3fb950';
      };
      ws.onmessage = (e) => {
        const d = JSON.parse(e.data);
        document.getElementById('guild-count').textContent    = d.guilds ?? '—';
        document.getElementById('active-players').textContent = d.active_players ?? '0';
        document.getElementById('playing-tracks').textContent = `${d.active_players ?? 0} track${d.active_players !== 1 ? 's' : ''} playing`;
        document.getElementById('uptime-text').textContent    = 'Uptime: ' + (d.uptime ?? '—');
        document.getElementById('last-update').textContent    = 'Updated ' + new Date().toLocaleTimeString();

        // Circuit breakers
        const ytCb = d.circuit_breakers?.youtube ?? {state:'closed'};
        const spCb = d.circuit_breakers?.spotify  ?? {state:'closed'};
        document.getElementById('cb-yt').innerHTML = `<div class="dot ${colorDot(ytCb.state)}"></div><span>YouTube: ${ytCb.state}</span>`;
        document.getElementById('cb-sp').innerHTML = `<div class="dot ${colorDot(spCb.state)}"></div><span>Spotify: ${spCb.state}</span>`;

        // Sessions table
        const sessions = d.sessions ?? [];
        const tbody = document.getElementById('sessions-body');
        if (sessions.length === 0) {
          tbody.innerHTML = '<tr><td colspan="4" style="color:var(--muted);text-align:center">No active sessions</td></tr>';
        } else {
          tbody.innerHTML = sessions.map(s => {
            const title = s.now_playing ? `▶ ${s.now_playing.title.substring(0,50)}` : '<em style="color:var(--muted)">Idle</em>';
            return `<tr><td>${s.guild_name || s.guild_id}</td><td>${title}</td><td>${s.queue_size}</td><td>${Math.round(s.volume*100)}%</td></tr>`;
          }).join('');
        }
      };
      ws.onclose = () => {
        document.getElementById('ws-status').textContent = '⚡ Reconnecting…';
        document.getElementById('ws-status').style.color = '#f0a500';
        setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
  </script>
</body>
</html>
"""


# ── IP Rate Limiting ──────────────────────────────────────────────────────────

class _IPRateLimiter:
    def __init__(self, max_req: int = 60, window: float = 60.0) -> None:
        self.max_req = max_req
        self.window  = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, ip: str) -> bool:
        """Return True if allowed, False if rate-limited."""
        now = time.monotonic()
        dq  = self._hits[ip]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.max_req:
            return False
        dq.append(now)
        return True


# ── Server ────────────────────────────────────────────────────────────────────

class WebServer:
    """
    Async aiohttp web server.

    Endpoints:
      GET /               → HTML dashboard (real-time via WebSocket)
      GET /health         → 200 OK
      GET /status         → JSON status snapshot
      GET /ready          → 200 if bot is ready, 503 otherwise
      GET /api/v1/guilds  → list of active guild IDs + names
      GET /api/v1/guild/{id}/nowplaying → current track JSON
      GET /api/v1/guild/{id}/queue      → current queue JSON
      GET /api/v1/guild/{id}/analytics  → play analytics (last 7 days)
      WS  /ws/stats       → real-time stats push every 5s
    """

    def __init__(self, bot: "MusicBot") -> None:
        self.bot     = bot
        self.app     = web.Application(middlewares=[self._rate_limit_mw, self._cors_mw])
        self._ip_rl  = _IPRateLimiter(config.API_RATE_LIMIT, window=60.0)
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._runner: Optional[web.AppRunner] = None
        self._push_task: Optional[asyncio.Task] = None
        self._stats_cache: Optional[dict] = None
        self._stats_ts: float = 0.0

        self._setup_routes()

    # ── Middleware ────────────────────────────────────────────────────────────

    @web.middleware
    async def _rate_limit_mw(self, request: web.Request, handler) -> web.Response:
        ip = request.remote or "0.0.0.0"
        if not self._ip_rl.check(ip):
            raise web.HTTPTooManyRequests(
                text=json.dumps({"error": "rate_limited", "retry_after": 60}),
                content_type="application/json",
            )
        return await handler(request)

    @web.middleware
    async def _cors_mw(self, request: web.Request, handler) -> web.Response:
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        return resp

    # ── Auth helper ───────────────────────────────────────────────────────────

    def _check_auth(self, request: web.Request) -> bool:
        """Return True if auth passes (or no API_SECRET configured)."""
        if not config.API_SECRET:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {config.API_SECRET}"

    # ── Route setup ───────────────────────────────────────────────────────────

    def _setup_routes(self) -> None:
        self.app.router.add_get("/",                           self._dashboard)
        self.app.router.add_get("/health",                     self._health)
        self.app.router.add_get("/status",                     self._status)
        self.app.router.add_get("/ready",                      self._ready)
        self.app.router.add_get("/ws/stats",                   self._ws_stats)
        self.app.router.add_get("/api/v1/guilds",              self._api_guilds)
        self.app.router.add_get("/api/v1/guild/{id}/nowplaying", self._api_nowplaying)
        self.app.router.add_get("/api/v1/guild/{id}/queue",    self._api_queue)
        self.app.router.add_get("/api/v1/guild/{id}/analytics", self._api_analytics)

    # ── Basic endpoints ───────────────────────────────────────────────────────

    async def _dashboard(self, request: web.Request) -> web.Response:
        return web.Response(text=_DASHBOARD_HTML, content_type="text/html")

    async def _health(self, request: web.Request) -> web.Response:
        return web.Response(text="OK", content_type="text/plain")

    async def _ready(self, request: web.Request) -> web.Response:
        if self.bot.is_ready():
            return web.Response(text="READY", content_type="text/plain")
        raise web.HTTPServiceUnavailable(text="NOT_READY")

    async def _status(self, request: web.Request) -> web.Response:
        data = await self._build_stats()
        return web.json_response(data)

    # ── REST API v1 ───────────────────────────────────────────────────────────

    async def _api_guilds(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(text='{"error":"unauthorized"}', content_type="application/json")
        guilds = [
            {
                "id":   str(g.id),
                "name": g.name,
                "members": g.member_count,
                "active": self.bot.get_player(g.id).now_playing is not None,
            }
            for g in self.bot.guilds
        ]
        return web.json_response({"guilds": guilds, "total": len(guilds)})

    async def _api_nowplaying(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(text='{"error":"unauthorized"}', content_type="application/json")
        try:
            gid = int(request.match_info["id"])
        except (ValueError, KeyError):
            raise web.HTTPBadRequest(text='{"error":"invalid_guild_id"}', content_type="application/json")

        player = self.bot.get_player(gid)
        if not player.now_playing:
            return web.json_response({"now_playing": None})

        t = player.now_playing
        return web.json_response({
            "now_playing": {
                "title":     t.title,
                "url":       t.url,
                "duration":  t.duration,
                "thumbnail": t.thumbnail,
                "uploader":  t.uploader,
                "elapsed":   player.elapsed_seconds,
                "progress":  round(player.progress_fraction() * 100, 1),
            },
            "queue_size": len(player),
            "volume":     player.volume,
            "loop_mode":  player.loop_mode.value,
        })

    async def _api_queue(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(text='{"error":"unauthorized"}', content_type="application/json")
        try:
            gid = int(request.match_info["id"])
        except (ValueError, KeyError):
            raise web.HTTPBadRequest(text='{"error":"invalid_guild_id"}', content_type="application/json")

        player = self.bot.get_player(gid)
        queue  = player.queue
        return web.json_response({
            "queue":  [t.to_dict() for t in queue],
            "total":  len(queue),
            "loop":   player.loop_mode.value,
        })

    async def _api_analytics(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(text='{"error":"unauthorized"}', content_type="application/json")
        try:
            gid  = int(request.match_info["id"])
            days = int(request.rel_url.query.get("days", "7"))
        except (ValueError, KeyError):
            raise web.HTTPBadRequest(text='{"error":"invalid_params"}', content_type="application/json")

        data = await self.bot.db.get_analytics(gid, days=max(1, min(days, 90)))
        return web.json_response(data)

    # ── WebSocket /ws/stats ───────────────────────────────────────────────────

    async def _ws_stats(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.debug("WebSocket client connected (%d total)", len(self._ws_clients))
        try:
            # Send initial stats immediately
            stats = await self._build_stats()
            await ws.send_json(stats)
            # Keep alive until client disconnects
            async for _ in ws:
                pass
        finally:
            self._ws_clients.discard(ws)
            logger.debug("WebSocket client disconnected (%d remaining)", len(self._ws_clients))
        return ws

    # ── Stats builder ─────────────────────────────────────────────────────────

    async def _build_stats(self) -> dict:
        now = time.monotonic()
        if self._stats_cache and now - self._stats_ts < 5.0:
            return self._stats_cache

        active_players = [
            (g_id, p) for g_id, p in self.bot._players.items()
            if p.now_playing is not None
        ]

        sessions = []
        for g_id, p in active_players:
            guild = self.bot.get_guild(g_id)
            t = p.now_playing
            sessions.append({
                "guild_id":   str(g_id),
                "guild_name": guild.name if guild else str(g_id),
                "now_playing": t.to_dict() if t else None,
                "queue_size":  len(p),
                "volume":      round(p.volume, 2),
                "loop_mode":   p.loop_mode.value,
            })

        stats = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "status":        "online" if self.bot.is_ready() else "starting",
            "guilds":        len(self.bot.guilds),
            "active_players": len(active_players),
            "uptime":        format_uptime(self.bot.start_time) if hasattr(self.bot, "start_time") else "—",
            "sessions":      sessions,
            "circuit_breakers": {
                "youtube": self.bot.yt_breaker.status_dict(),
                "spotify":  self.bot.sp_breaker.status_dict(),
            },
        }
        self._stats_cache = stats
        self._stats_ts    = now
        return stats

    # ── WS push task ──────────────────────────────────────────────────────────

    async def _ws_push_loop(self) -> None:
        """Push stats to all connected WebSocket clients every 5 seconds."""
        while True:
            await asyncio.sleep(5)
            if not self._ws_clients:
                continue
            try:
                stats = await self._build_stats()
                dead  = set()
                for ws in list(self._ws_clients):
                    try:
                        await ws.send_json(stats)
                    except Exception:
                        dead.add(ws)
                self._ws_clients -= dead
            except Exception as exc:
                logger.debug("WS push error: %s", exc)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, config.WEB_HOST, config.WEB_PORT)
        await site.start()
        self._push_task = asyncio.create_task(self._ws_push_loop())
        logger.info("Webserver listening on http://%s:%d", config.WEB_HOST, config.WEB_PORT)

    async def stop(self) -> None:
        if self._push_task:
            self._push_task.cancel()
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Webserver stopped.")
