# -*- coding: utf-8 -*-
"""
dashboard/websocket.py — Local WebSocket manager for real-time dashboard updates.

Handles bi-directional communication:
  - Pushes playback state, elapsed time, and queue changes in real-time
  - Receives player control actions from web clients
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional

from aiohttp import web

if TYPE_CHECKING:
    from dashboard.service import DashboardService

logger = logging.getLogger(__name__)


class DashboardWebSocketManager:
    """Manages WebSocket connections and message routing for the local dashboard."""

    def __init__(self, service: "DashboardService") -> None:
        self.service = service
        self._clients: set[web.WebSocketResponse] = set()
        self._client_guilds: dict[web.WebSocketResponse, Optional[int]] = {}
        self._push_task: Optional[asyncio.Task] = None

        # Register listener on service so state changes are broadcast automatically
        self.service.register_listener(self.broadcast_guild_state)

    async def start(self) -> None:
        """Start the periodic progress sync task."""
        if not self._push_task or self._push_task.done():
            self._push_task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        """Close all connections and stop the sync task."""
        if self._push_task:
            self._push_task.cancel()
            self._push_task = None

        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        self._client_guilds.clear()

    async def handle_connection(self, request: web.Request) -> web.WebSocketResponse:
        """Handle incoming WebSocket connection from web dashboard."""
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)

        self._clients.add(ws)
        self._client_guilds[ws] = None
        logger.debug("Dashboard WS client connected (%d total)", len(self._clients))

        try:
            # Send initial payload: all guilds + server info
            guilds = self.service.get_all_guilds()
            init_msg = {
                "type": "init",
                "guilds": guilds,
            }

            # If client passed ?guild_id in URL, select it immediately
            requested_gid = request.rel_url.query.get("guild_id")
            if requested_gid:
                try:
                    gid_int = int(requested_gid)
                    self._client_guilds[ws] = gid_int
                    init_msg["selected_guild"] = self.service.get_guild_state(gid_int)
                except ValueError:
                    pass
            elif guilds:
                # Default to first guild with active player, or first guild
                active_gid = None
                for g in guilds:
                    if g["active"]:
                        active_gid = int(g["id"])
                        break
                first_gid = active_gid or int(guilds[0]["id"])
                self._client_guilds[ws] = first_gid
                init_msg["selected_guild"] = self.service.get_guild_state(first_gid)

            await ws.send_json(init_msg)

            # Listen for client messages
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_client_message(ws, data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    except Exception as exc:
                        logger.error("Error processing dashboard WS message: %s", exc)
                        await ws.send_json({"type": "error", "message": str(exc)})
                elif msg.type == web.WSMsgType.ERROR:
                    logger.debug("Dashboard WS connection error: %s", ws.exception())
        finally:
            self._clients.discard(ws)
            self._client_guilds.pop(ws, None)
            logger.debug("Dashboard WS client disconnected (%d remaining)", len(self._clients))

        return ws

    async def _handle_client_message(
        self, ws: web.WebSocketResponse, data: dict
    ) -> None:
        """Dispatch actions received from client."""
        action = data.get("action") or data.get("type")
        guild_id_raw = data.get("guild_id") or self._client_guilds.get(ws)

        if not action:
            return

        # ── Guild Selection ───────────────────────────────────────────────────
        if action in ("select_guild", "subscribe"):
            try:
                gid = int(data.get("guild_id", 0))
                self._client_guilds[ws] = gid
                state = self.service.get_guild_state(gid)
                await ws.send_json({"type": "guild_state", "data": state})
            except (ValueError, TypeError):
                await ws.send_json({"type": "error", "message": "Invalid guild_id"})
            return

        if not guild_id_raw:
            await ws.send_json({"type": "error", "message": "No guild selected"})
            return

        try:
            guild_id = int(guild_id_raw)
        except (ValueError, TypeError):
            await ws.send_json({"type": "error", "message": "Invalid guild_id"})
            return

        # ── Player Actions ────────────────────────────────────────────────────
        result = None
        if action == "pause":
            result = await self.service.pause_or_resume(guild_id, "pause")
        elif action == "resume":
            result = await self.service.pause_or_resume(guild_id, "resume")
        elif action in ("toggle_pause", "play_pause"):
            result = await self.service.pause_or_resume(guild_id, None)
        elif action == "skip":
            result = await self.service.skip(guild_id)
        elif action == "volume":
            vol = float(data.get("volume", data.get("value", 100)))
            result = await self.service.set_volume(guild_id, vol)
        elif action == "seek":
            pos = int(data.get("position", data.get("seconds", 0)))
            result = await self.service.seek(guild_id, pos)
        elif action == "move_queue":
            from_idx = int(data.get("from_index", data.get("from", 0)))
            to_idx = int(data.get("to_index", data.get("to", 0)))
            result = await self.service.move_queue(guild_id, from_idx, to_idx)
        elif action == "remove_queue":
            idx = int(data.get("index", -1))
            result = await self.service.remove_from_queue(guild_id, idx)
        elif action == "clear_queue":
            result = await self.service.clear_queue(guild_id)
        elif action == "shuffle_queue":
            result = await self.service.shuffle_queue(guild_id)
        elif action == "loop":
            mode = data.get("mode", "off")
            result = await self.service.set_loop_mode(guild_id, mode)
        elif action in ("toggle_effect", "effect"):
            effect = data.get("effect", "")
            result = await self.service.toggle_effect(guild_id, effect)
        elif action == "add_to_queue":
            query = data.get("query", "")
            play_next = bool(data.get("play_next", False))
            result = await self.service.add_to_queue(guild_id, query, play_next=play_next)
        elif action == "play_next":
            query = data.get("query", "")
            result = await self.service.add_to_queue(guild_id, query, play_next=True)
        elif action == "search":
            query = data.get("query", "")
            limit = int(data.get("limit", 5))
            results = await self.service.search_tracks(query, limit=limit)
            await ws.send_json({"type": "search_results", "query": query, "results": results})
            return
        else:
            await ws.send_json({"type": "error", "message": f"Unknown action: {action}"})
            return

        if result:
            await ws.send_json({"type": "action_result", "action": action, "result": result})

    async def broadcast_guild_state(self, guild_id: int, state: dict) -> None:
        """Broadcast state update to all clients viewing this guild or in broad view."""
        msg = {
            "type": "guild_state",
            "guild_id": str(guild_id),
            "data": state,
        }
        dead: set[web.WebSocketResponse] = set()
        for ws in list(self._clients):
            sub_gid = self._client_guilds.get(ws)
            # Send if client is viewing this guild or hasn't selected one
            if sub_gid is None or sub_gid == guild_id:
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.add(ws)

        self._clients -= dead
        for ws in dead:
            self._client_guilds.pop(ws, None)

    async def _sync_loop(self) -> None:
        """Periodic loop (every 3s) pushing elapsed progress to active sessions."""
        try:
            while True:
                await asyncio.sleep(3.0)
                if not self._clients:
                    continue

                # Collect unique active subscribed guild IDs
                active_gids = set()
                for ws, gid in self._client_guilds.items():
                    if gid:
                        active_gids.add(gid)

                for gid in active_gids:
                    state = self.service.get_guild_state(gid)
                    # Only broadcast if track is playing
                    if state.get("is_playing"):
                        await self.broadcast_guild_state(gid, state)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Dashboard WS sync loop error: %s", exc)
