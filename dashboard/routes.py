# -*- coding: utf-8 -*-
"""
dashboard/routes.py — REST API and Web routes for the Local Web Dashboard.

Provides endpoints for:
  - Interactive Web Player: /pause, /resume, /skip, /volume, /seek
  - Drag-and-Drop Queue: /queue/move, /queue/{index}
  - WebSocket: /ws/dashboard
  - Web UI: / and /dashboard
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

from aiohttp import web

import config
from dashboard.templates import render_dashboard_html

if TYPE_CHECKING:
    from dashboard.service import DashboardService
    from dashboard.websocket import DashboardWebSocketManager

logger = logging.getLogger(__name__)


class DashboardRouter:
    """Registers and handles HTTP and WebSocket routes for the local dashboard."""

    def __init__(
        self,
        service: "DashboardService",
        ws_manager: "DashboardWebSocketManager",
    ) -> None:
        self.service = service
        self.ws_manager = ws_manager

    def _check_auth(self, request: web.Request) -> bool:
        """Check Bearer token or URL token query param if API_SECRET is configured."""
        if not config.API_SECRET:
            return True

        # Header check
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {config.API_SECRET}":
            return True

        # Query param check (?token=... or ?secret=...)
        token = request.rel_url.query.get("token") or request.rel_url.query.get("secret")
        if token == config.API_SECRET:
            return True

        return False

    def _require_auth(self, request: web.Request) -> None:
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "unauthorized"}),
                content_type="application/json",
            )

    def _parse_guild_id(self, request: web.Request) -> int:
        try:
            return int(request.match_info["id"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "invalid_guild_id"}),
                content_type="application/json",
            )

    async def _parse_json_body(self, request: web.Request) -> dict:
        try:
            if request.can_read_body:
                return await request.json()
            return {}
        except Exception:
            return {}

    # ── Web UI Handlers ───────────────────────────────────────────────────────

    async def handle_dashboard_page(self, request: web.Request) -> web.Response:
        """Serve the self-contained offline Vanilla HTML/CSS/JS dashboard."""
        html = render_dashboard_html(port=config.WEB_PORT, api_secret=config.API_SECRET)
        return web.Response(text=html, content_type="text/html")

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Handle dashboard WebSocket connection."""
        if not self._check_auth(request):
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "unauthorized"}),
                content_type="application/json",
            )
        return await self.ws_manager.handle_connection(request)

    # ── Guild & State Endpoints ───────────────────────────────────────────────

    async def api_get_guilds(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        guilds = self.service.get_all_guilds()
        return web.json_response({"guilds": guilds, "total": len(guilds)})

    async def api_get_guild_state(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        state = self.service.get_guild_state(gid)
        return web.json_response(state)

    # ── Interactive Player Endpoints ──────────────────────────────────────────

    async def api_pause(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        body = await self._parse_json_body(request)
        action = body.get("action", "pause")
        res = await self.service.pause_or_resume(gid, action=action)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    async def api_resume(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        res = await self.service.pause_or_resume(gid, action="resume")
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    async def api_skip(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        res = await self.service.skip(gid)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    async def api_volume(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        body = await self._parse_json_body(request)
        vol_raw = body.get("volume")
        if vol_raw is None:
            vol_raw = request.rel_url.query.get("volume", "100")
        try:
            vol = float(vol_raw)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "invalid_volume"}),
                content_type="application/json",
            )
        res = await self.service.set_volume(gid, vol)
        return web.json_response(res)

    async def api_seek(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        body = await self._parse_json_body(request)
        pos_raw = body.get("position", body.get("seconds"))
        if pos_raw is None:
            pos_raw = request.rel_url.query.get("position", request.rel_url.query.get("seconds"))
        try:
            pos = int(pos_raw)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "invalid_position"}),
                content_type="application/json",
            )
        res = await self.service.seek(gid, pos)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    # ── Queue Endpoints ───────────────────────────────────────────────────────

    async def api_queue_move(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        body = await self._parse_json_body(request)
        try:
            from_idx = int(body.get("from_index", body.get("from", -1)))
            to_idx = int(body.get("to_index", body.get("to", -1)))
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "invalid_indices"}),
                content_type="application/json",
            )
        res = await self.service.move_queue(gid, from_idx, to_idx)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    async def api_queue_remove(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        try:
            idx = int(request.match_info["index"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "invalid_index"}),
                content_type="application/json",
            )
        res = await self.service.remove_from_queue(gid, idx)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    async def api_queue_clear(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        res = await self.service.clear_queue(gid)
        return web.json_response(res)

    async def api_queue_shuffle(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        gid = self._parse_guild_id(request)
        res = await self.service.shuffle_queue(gid)
        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)

    # ── Router Registration ───────────────────────────────────────────────────

    def register_routes(self, app: web.Application) -> None:
        """Register all dashboard routes with an aiohttp Application."""
        # Pages & WebSocket
        app.router.add_get("/", self.handle_dashboard_page)
        app.router.add_get("/dashboard", self.handle_dashboard_page)
        app.router.add_get("/ws/dashboard", self.handle_ws)

        # Dashboard REST API
        app.router.add_get("/api/v1/dashboard/guilds", self.api_get_guilds)
        app.router.add_get("/api/v1/guild/{id}/state", self.api_get_guild_state)

        # Player Controls
        app.router.add_post("/api/v1/guild/{id}/pause", self.api_pause)
        app.router.add_post("/api/v1/guild/{id}/resume", self.api_resume)
        app.router.add_post("/api/v1/guild/{id}/skip", self.api_skip)
        app.router.add_post("/api/v1/guild/{id}/volume", self.api_volume)
        app.router.add_post("/api/v1/guild/{id}/seek", self.api_seek)

        # Queue Management
        app.router.add_post("/api/v1/guild/{id}/queue/move", self.api_queue_move)
        app.router.add_delete("/api/v1/guild/{id}/queue/{index}", self.api_queue_remove)
        app.router.add_post("/api/v1/guild/{id}/queue/clear", self.api_queue_clear)
        app.router.add_post("/api/v1/guild/{id}/queue/shuffle", self.api_queue_shuffle)
