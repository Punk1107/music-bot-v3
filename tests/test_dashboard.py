# -*- coding: utf-8 -*-
"""
tests/test_dashboard.py — Unit and integration tests for Group 4: Local Web Dashboard.

Tests:
  - DashboardService: playback controls (pause, resume, skip), volume, seek, queue drag-and-drop move/remove
  - DashboardWebSocketManager: client lifecycle, message handling, broadcast
  - DashboardRouter & REST Endpoints: /pause, /skip, /volume, /seek, /queue/move, auth handling
  - HTML Template: 100% offline compliance (no external CDNs, fonts, or scripts)
  - DashboardCog: /dashboard slash command
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import config
from core.player import GuildPlayer
from dashboard.cog import DashboardCog
from dashboard.routes import DashboardRouter
from dashboard.service import DashboardService
from dashboard.templates import render_dashboard_html
from dashboard.websocket import DashboardWebSocketManager
from equalizer.presets import EQ_PRESETS
from models.enums import AudioEffect, LoopMode
from models.track import Track


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def dummy_track():
    return Track(
        title="Test Song",
        url="https://youtube.com/watch?v=12345",
        duration=180,
        thumbnail="https://example.com/thumb.jpg",
        uploader="Test Artist",
        requested_by_id=12345,
        requested_by_name="User#0001",
    )


@pytest.fixture
def mock_bot(dummy_track):
    bot = MagicMock()
    bot.guilds = []
    bot._players = {}

    player = GuildPlayer(111)
    bot._players[111] = player
    bot.get_player = MagicMock(side_effect=lambda gid: bot._players.setdefault(gid, GuildPlayer(gid)))

    mock_guild = MagicMock()
    mock_guild.id = 111
    mock_guild.name = "Test Guild"
    mock_guild.icon = None

    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    mock_vc.is_paused.return_value = False
    mock_vc.channel.name = "General Voice"
    mock_vc.source = MagicMock()
    mock_vc.source.volume = 1.0

    mock_guild.voice_client = mock_vc
    bot.guilds.append(mock_guild)
    bot.get_guild = MagicMock(return_value=mock_guild)

    # Mock seek service on bot
    bot.seek = MagicMock()
    bot.seek.seek_to = AsyncMock(return_value=True)
    bot.seek.hot_reload = AsyncMock(return_value=True)

    # Mock youtube on bot
    bot.youtube = MagicMock()
    bot.youtube.search = AsyncMock(return_value=[dummy_track])
    bot.youtube.get_track = AsyncMock(return_value=dummy_track)
    bot.youtube.is_youtube_url = MagicMock(return_value=False)
    bot.youtube.is_playlist_url = MagicMock(return_value=False)
    bot.spotify = MagicMock()
    bot.spotify.is_spotify_url = MagicMock(return_value=False)
    bot.http_session = MagicMock()
    bot.get_cog = MagicMock(return_value=None)

    # Mock DB on bot
    cfg_mock = MagicMock()
    cfg_mock.guild_presets = {}
    cfg_mock.audio_quality = MagicMock(value="high")
    bot.db = MagicMock()
    bot.db.get_server_config = AsyncMock(return_value=cfg_mock)
    bot.db.save_server_config = AsyncMock()
    bot.db.save_queue = AsyncMock()
    bot.db.clear_queue = AsyncMock()

    import asyncio
    def _track_task(task_or_coro, *args, **kwargs):
        if asyncio.iscoroutine(task_or_coro):
            task_or_coro.close()
        return MagicMock()
    bot.track_task = MagicMock(side_effect=_track_task)

    return bot


# ── Test DashboardService ─────────────────────────────────────────────────────

class TestDashboardService:
    @pytest.mark.asyncio
    async def test_get_all_guilds(self, mock_bot):
        service = DashboardService(mock_bot)
        guilds = service.get_all_guilds()
        assert len(guilds) == 1
        assert guilds[0]["id"] == "111"
        assert guilds[0]["name"] == "Test Guild"
        assert guilds[0]["voice_connected"] is True
        assert guilds[0]["voice_channel"] == "General Voice"

    @pytest.mark.asyncio
    async def test_get_guild_state_idle(self, mock_bot):
        service = DashboardService(mock_bot)
        state = service.get_guild_state(111)
        assert state["guild_id"] == "111"
        assert state["now_playing"] is None
        assert state["queue"] == []
        assert state["queue_size"] == 0
        assert state["volume_percent"] == 100

    @pytest.mark.asyncio
    async def test_get_guild_state_playing(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track
        await player.enqueue(Track(title="Queued Song", url="https://ex.com", duration=120, requested_by_name="QueueUser"))

        state = service.get_guild_state(111)
        assert state["now_playing"] is not None
        assert state["now_playing"]["title"] == "Test Song"
        assert state["now_playing"]["duration"] == 180
        assert state["now_playing"]["requested_by"] == "User#0001"
        assert len(state["queue"]) == 1
        assert state["queue"][0]["title"] == "Queued Song"
        assert state["queue"][0]["requested_by"] == "QueueUser"
        assert state["queue_size"] == 1

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, mock_bot):
        service = DashboardService(mock_bot)
        vc = mock_bot.get_guild(111).voice_client

        # Toggle to pause
        res_pause = await service.pause_or_resume(111, action="pause")
        assert res_pause["success"] is True
        assert res_pause["paused"] is True
        vc.pause.assert_called_once()

        # Resume
        vc.is_playing.return_value = False
        vc.is_paused.return_value = True
        res_resume = await service.pause_or_resume(111, action="resume")
        assert res_resume["success"] is True
        assert res_resume["paused"] is False
        vc.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track
        vc = mock_bot.get_guild(111).voice_client

        res = await service.skip(111)
        assert res["success"] is True
        assert res["skipped_track"] == "Test Song"
        vc.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        vc = mock_bot.get_guild(111).voice_client

        # Percentage 85%
        res = await service.set_volume(111, 85)
        assert res["success"] is True
        assert res["volume"] == 0.85
        assert res["volume_percent"] == 85
        assert player.volume == 0.85
        assert vc.source.volume == 0.85

        # Ratio 1.5
        res2 = await service.set_volume(111, 1.5)
        assert res2["volume"] == 1.5
        assert res2["volume_percent"] == 150

        # Clamped out-of-range
        res3 = await service.set_volume(111, 250)
        assert res3["volume"] == 2.0

    @pytest.mark.asyncio
    async def test_seek(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track

        res = await service.seek(111, 45)
        assert res["success"] is True
        assert res["position"] == 45
        mock_bot.seek.seek_to.assert_called_once_with(111, 45)

    @pytest.mark.asyncio
    async def test_move_queue_drag_and_drop(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        t0 = Track(title="Song 0", url="https://ex.com/0", duration=100)
        t1 = Track(title="Song 1", url="https://ex.com/1", duration=100)
        t2 = Track(title="Song 2", url="https://ex.com/2", duration=100)
        await player.extend([t0, t1, t2])

        # Drag item 0 to position 2
        res = await service.move_queue(111, from_index=0, to_index=2)
        assert res["success"] is True
        assert [t.title for t in player.queue] == ["Song 1", "Song 2", "Song 0"]

        # Undo stack verification
        assert len(player.undo_stack) == 1
        assert player.undo_stack[-1].operation == "move"

        # Out of bounds should fail
        err_res = await service.move_queue(111, from_index=0, to_index=99)
        assert err_res["success"] is False

    @pytest.mark.asyncio
    async def test_remove_from_queue(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        t0 = Track(title="Song 0", url="https://ex.com/0", duration=100)
        t1 = Track(title="Song 1", url="https://ex.com/1", duration=100)
        await player.extend([t0, t1])

        res = await service.remove_from_queue(111, index=0)
        assert res["success"] is True
        assert res["removed"]["title"] == "Song 0"
        assert len(player) == 1
        assert player.queue[0].title == "Song 1"

    @pytest.mark.asyncio
    async def test_clear_and_shuffle_queue(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        await player.extend([
            Track(title=f"Song {i}", url=f"https://ex.com/{i}", duration=60)
            for i in range(5)
        ])

        shuf_res = await service.shuffle_queue(111)
        assert shuf_res["success"] is True

        clear_res = await service.clear_queue(111)
        assert clear_res["success"] is True
        assert clear_res["cleared_count"] == 5
        assert len(player) == 0

    @pytest.mark.asyncio
    async def test_set_loop_mode(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)

        res = await service.set_loop_mode(111, "track")
        assert res["success"] is True
        assert player.loop_mode == LoopMode.TRACK

        res_invalid = await service.set_loop_mode(111, "invalid_mode")
        assert res_invalid["success"] is False

    @pytest.mark.asyncio
    async def test_toggle_effect(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)

        res = await service.toggle_effect(111, "bass_boost")
        assert res["success"] is True
        assert res["enabled"] is True
        assert AudioEffect.BASS_BOOST in player.effects

        # Toggle off
        res_off = await service.toggle_effect(111, "bass_boost")
        assert res_off["success"] is True
        assert res_off["enabled"] is False
        assert AudioEffect.BASS_BOOST not in player.effects

    @pytest.mark.asyncio
    async def test_search_and_add_to_queue(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)

        # Search
        results = await service.search_tracks("Test Song")
        assert len(results) == 1
        assert results[0]["title"] == "Test Song"

        # Add to queue
        add_res = await service.add_to_queue(111, "Test Song", play_next=False)
        assert add_res["success"] is True
        assert len(player) == 1

        # Play next
        add_next = await service.add_to_queue(111, "Test Song", play_next=True)
        assert add_next["success"] is True
        assert len(player) == 2


# ── Test WebSocket Manager ────────────────────────────────────────────────────

class TestDashboardWebSocketManager:
    @pytest.mark.asyncio
    async def test_websocket_actions_dispatch(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        ws_mgr = DashboardWebSocketManager(service)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track

        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        ws_mgr._client_guilds[mock_ws] = 111

        # Test volume action
        await ws_mgr._handle_client_message(mock_ws, {"action": "volume", "volume": 70})
        assert player.volume == 0.7
        mock_ws.send_json.assert_called()

        # Test skip action
        await ws_mgr._handle_client_message(mock_ws, {"action": "skip"})
        mock_bot.get_guild(111).voice_client.stop.assert_called()

        await ws_mgr.stop()


# ── Test REST Endpoints via Aiohttp ───────────────────────────────────────────

@pytest.fixture
def app(mock_bot, dummy_track, monkeypatch):
    monkeypatch.setattr(config, "API_SECRET", "")
    service = DashboardService(mock_bot)
    ws_mgr = DashboardWebSocketManager(service)
    router = DashboardRouter(service, ws_mgr)

    player = mock_bot.get_player(111)
    player.now_playing = dummy_track

    app_instance = web.Application()
    router.register_routes(app_instance)
    return app_instance


class TestDashboardRoutes:

    @pytest.mark.asyncio
    async def test_get_dashboard_page(self, app):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/")
            assert resp.status == 200
            text = await resp.text()
            assert "<!DOCTYPE html>" in text
            assert "Music Bot V3" in text
            assert "Interactive Player" in text
            assert "Queue Management" in text

    @pytest.mark.asyncio
    async def test_api_guilds(self, app):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/dashboard/guilds")
            assert resp.status == 200
            data = await resp.json()
            assert data["total"] == 1
            assert data["guilds"][0]["id"] == "111"

    @pytest.mark.asyncio
    async def test_api_player_controls(self, app, mock_bot):
        async with TestClient(TestServer(app)) as client:
            vc = mock_bot.get_guild(111).voice_client

            # POST /pause
            resp_pause = await client.post("/api/v1/guild/111/pause", json={})
            assert resp_pause.status == 200
            vc.pause.assert_called()

            # POST /volume
            resp_vol = await client.post("/api/v1/guild/111/volume", json={"volume": 60})
            assert resp_vol.status == 200
            data_vol = await resp_vol.json()
            assert data_vol["volume"] == 0.6
            assert data_vol["volume_percent"] == 60

            # POST /seek
            resp_seek = await client.post("/api/v1/guild/111/seek", json={"position": 30})
            assert resp_seek.status == 200
            mock_bot.seek.seek_to.assert_called_with(111, 30)

            # POST /skip
            resp_skip = await client.post("/api/v1/guild/111/skip")
            assert resp_skip.status == 200
            vc.stop.assert_called()

    @pytest.mark.asyncio
    async def test_api_queue_move_and_delete(self, app, mock_bot):
        async with TestClient(TestServer(app)) as client:
            player = mock_bot.get_player(111)
            await player.extend([
                Track(title="Song 1", url="https://ex.com/1", duration=100),
                Track(title="Song 2", url="https://ex.com/2", duration=100),
            ])

            # POST /queue/move
            resp_move = await client.post(
                "/api/v1/guild/111/queue/move",
                json={"from_index": 0, "to_index": 1},
            )
            assert resp_move.status == 200
            assert player.queue[0].title == "Song 2"

            # DELETE /queue/0
            resp_del = await client.delete("/api/v1/guild/111/queue/0")
            assert resp_del.status == 200
            assert len(player) == 1
            assert player.queue[0].title == "Song 1"

    @pytest.mark.asyncio
    async def test_api_advanced_controls(self, app, mock_bot):
        async with TestClient(TestServer(app)) as client:
            player = mock_bot.get_player(111)

            # POST /loop
            resp_loop = await client.post("/api/v1/guild/111/loop", json={"mode": "track"})
            assert resp_loop.status == 200
            assert player.loop_mode == LoopMode.TRACK

            # POST /effects
            resp_eff = await client.post("/api/v1/guild/111/effects", json={"effect": "vaporwave"})
            assert resp_eff.status == 200
            assert AudioEffect.VAPORWAVE in player.effects

            # GET /dashboard/search
            resp_search = await client.get("/api/v1/dashboard/search?q=test")
            assert resp_search.status == 200
            data = await resp_search.json()
            assert "results" in data

            # POST /queue/add
            resp_add = await client.post("/api/v1/guild/111/queue/add", json={"query": "test query"})
            assert resp_add.status == 200
            assert len(player) >= 1

    @pytest.mark.asyncio
    async def test_auth_with_api_secret(self, mock_bot):
        service = DashboardService(mock_bot)
        ws_mgr = DashboardWebSocketManager(service)
        router = DashboardRouter(service, ws_mgr)

        app = web.Application()
        router.register_routes(app)

        with patch.object(config, "API_SECRET", "super_secret_token"):
            async with TestClient(TestServer(app)) as client:
                # Unauthenticated -> 401
                resp_unauth = await client.get("/api/v1/dashboard/guilds")
                assert resp_unauth.status == 401

                # Bearer token -> 200
                resp_bearer = await client.get(
                    "/api/v1/dashboard/guilds",
                    headers={"Authorization": "Bearer super_secret_token"},
                )
                assert resp_bearer.status == 200

                # URL token param -> 200
                resp_query = await client.get("/api/v1/dashboard/guilds?token=super_secret_token")
                assert resp_query.status == 200


# ── Test Offline First Compliance ─────────────────────────────────────────────

class TestOfflineCompliance:
    def test_no_external_resources_in_template(self):
        html = render_dashboard_html()
        # Verify no external CDN fonts or scripts
        assert "fonts.googleapis.com" not in html
        assert "fonts.gstatic.com" not in html
        assert "cdn.jsdelivr.net" not in html
        assert "cdnjs.cloudflare.com" not in html
        assert "unpkg.com" not in html

        # Verify HTML5 Drag and Drop attributes
        assert 'draggable' in html
        assert 'dragstart' in html
        assert 'dragover' in html
        assert 'drop' in html

        # Verify system font stack
        assert '-apple-system' in html
        assert 'BlinkMacSystemFont' in html

    def test_new_ui_elements_in_template(self):
        html = render_dashboard_html()
        assert 'soundwave' in html
        assert 'data-theme' in html
        assert 'lyrics-drawer' in html
        assert 'shortcuts-modal' in html
        assert 'clear-modal' in html
        assert 'effects-pills' in html
        assert 'search-input' in html
        assert 'item-playnext' in html


# ── Test Discord Cog ──────────────────────────────────────────────────────────

class TestDashboardCog:
    @pytest.mark.asyncio
    async def test_dashboard_slash_command(self, mock_bot):
        cog = DashboardCog(mock_bot)
        interaction = MagicMock()
        interaction.guild_id = 111
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.dashboard.callback(cog, interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        interaction.followup.send.assert_called_once()
        _, kwargs = interaction.followup.send.call_args
        embed = kwargs.get("embed")
        assert embed is not None
        assert "Local Web Dashboard" in embed.title
        assert f"localhost:{config.WEB_PORT}" in embed.description
        assert kwargs.get("ephemeral") is True


# ── Test Next-Gen Web Dashboard 2.0 ──────────────────────────────────────────

class TestDashboardNextGenV2:
    """Comprehensive test suite for Phase 4: Next-Gen Web Dashboard 2.0."""

    @pytest.mark.asyncio
    async def test_equalizer_bands_and_clamping(self, mock_bot):
        service = DashboardService(mock_bot)

        # 1. Custom bands setting with clamping
        res = await service.set_equalizer(
            111,
            bands={"sub_bass": 5.0, "bass": -2.5, "mid": 3.0, "treble": 99.0},
        )
        assert res["success"] is True
        assert res["equalizer_bands"]["sub_bass"] == 5.0
        assert res["equalizer_bands"]["bass"] == -2.5
        assert res["equalizer_bands"]["mid"] == 3.0
        # Clamped to +10.0 dB
        assert res["equalizer_bands"]["treble"] == 10.0
        assert res["equalizer_preset"] == "custom"
        assert mock_bot.seek.hot_reload.called

        # 2. Studio preset application
        res_preset = await service.set_equalizer(111, preset="rock")
        assert res_preset["success"] is True
        assert res_preset["equalizer_preset"] == "rock"
        assert res_preset["equalizer_bands"] == EQ_PRESETS["rock"]

        # 3. Invalid preset
        res_inv = await service.set_equalizer(111, preset="nonexistent_preset")
        assert res_inv["success"] is False

        # 4. Custom preset loaded from MySQL
        cfg_mock = await mock_bot.db.get_server_config(111)
        cfg_mock.guild_presets = {
            "my_club": {
                "bands": {"sub_bass": 6.0, "bass": 4.0, "mid": 0.0, "treble": 2.0},
                "effects": ["bassboost"],
                "volume": 1.2,
            }
        }
        res_custom = await service.set_equalizer(111, preset="my_club")
        assert res_custom["success"] is True

    @pytest.mark.asyncio
    async def test_mysql_presets_crud_lifecycle(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.volume = 0.85
        player.equalizer_bands = {"sub_bass": 4.0, "bass": 2.0, "mid": 0.0, "treble": 3.0}

        # 1. Save custom preset
        res_save = await service.save_guild_preset(111, "StudioSession")
        assert res_save["success"] is True
        assert res_save["name"] == "studiosession"
        assert mock_bot.db.save_server_config.called

        # Verify saved in config
        cfg = await mock_bot.db.get_server_config(111)
        assert "studiosession" in cfg.guild_presets
        assert cfg.guild_presets["studiosession"]["volume"] == 0.85

        # 2. Get presets list
        res_list = await service.get_guild_presets(111)
        assert res_list["success"] is True
        assert "gaming" in res_list["presets"]
        assert "studiosession" in res_list["presets"]

        # 3. Load preset
        player.volume = 0.20
        res_load = await service.load_guild_preset(111, "studiosession")
        assert res_load["success"] is True
        assert player.volume == 0.85
        assert player.equalizer_bands["sub_bass"] == 4.0

        # 4. Delete preset
        res_del = await service.delete_guild_preset(111, "studiosession")
        assert res_del["success"] is True
        assert "studiosession" not in cfg.guild_presets

        # 5. Cannot delete built-in preset
        res_del_builtin = await service.delete_guild_preset(111, "gaming")
        assert res_del_builtin["success"] is False

    @pytest.mark.asyncio
    async def test_18_effects_toggle_and_all_effects_state(self, mock_bot):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)

        # Toggle effects
        res1 = await service.toggle_effect(111, "nightcore")
        assert res1["success"] is True
        assert res1["enabled"] is True
        assert AudioEffect.NIGHTCORE in player.effects

        res2 = await service.toggle_effect(111, "8d_audio")
        assert res2["success"] is True
        assert res2["enabled"] is True

        # Check state includes all 18 effects with enabled boolean for LED indicator
        state = service.get_guild_state(111)
        assert "all_effects" in state
        assert len(state["all_effects"]) == 18
        nc_info = next(e for e in state["all_effects"] if e["id"] == "nightcore")
        assert nc_info["enabled"] is True
        bb_info = next(e for e in state["all_effects"] if e["id"] == "bass_boost")
        assert bb_info["enabled"] is False

    @pytest.mark.asyncio
    async def test_synced_karaoke_lyrics_and_click_to_seek(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track

        mock_line = MagicMock()
        mock_line.start_sec = 42.0
        mock_line.timestamp_str = "0:42"
        mock_line.text = "Hello from the other side"

        with patch("lyrics.service.LyricsService.get_lyrics", AsyncMock(return_value=([mock_line], False))):
            res = await service.get_lyrics(111)
            assert res["success"] is True
            assert len(res["lines"]) == 1
            assert res["lines"][0]["start"] == 42.0
            assert res["lines"][0]["text"] == "Hello from the other side"

        # Click-to-seek jump to line timestamp
        seek_res = await service.seek(111, int(res["lines"][0]["start"]))
        assert seek_res["success"] is True
        assert seek_res["position"] == 42
        mock_bot.seek.seek_to.assert_called_with(111, 42)

    @pytest.mark.asyncio
    async def test_web_search_and_direct_enqueue_persists_mysql(self, mock_bot, dummy_track):
        service = DashboardService(mock_bot)
        player = mock_bot.get_player(111)

        # 1. Search tracks
        results = await service.search_tracks("Test Song", limit=3)
        assert len(results) >= 1
        assert results[0]["title"] == "Test Song"

        # 2. Add to queue directly from web search bar
        res_add = await service.add_to_queue(111, "https://youtube.com/watch?v=12345", play_next=False)
        assert res_add["success"] is True
        assert len(player.queue) == 1
        assert player.queue[0].title == "Test Song"
        # Verify persisted immediately to MySQL queue table
        assert mock_bot.db.save_queue.called

        # 3. Play next directly from web
        t2 = Track(title="Next Track", url="https://youtube.com/watch?v=next", duration=200)
        mock_bot.youtube.get_track = AsyncMock(return_value=t2)
        mock_bot.youtube.is_youtube_url = MagicMock(return_value=True)

        res_next = await service.add_to_queue(111, "https://youtube.com/watch?v=next", play_next=True)
        assert res_next["success"] is True
        assert player.queue[0].title == "Next Track"

    @pytest.mark.asyncio
    async def test_rest_api_equalizer_and_presets_endpoints(self, app):
        async with TestClient(TestServer(app)) as client:
            # 1. Set Equalizer
            eq_resp = await client.post(
                "/api/v1/guild/111/equalizer",
                json={"bands": {"sub_bass": 3.0, "bass": 1.5}},
            )
            assert eq_resp.status == 200
            eq_data = await eq_resp.json()
            assert eq_data["success"] is True
            assert eq_data["equalizer_bands"]["sub_bass"] == 3.0

            # 2. Save Preset
            save_resp = await client.post(
                "/api/v1/guild/111/presets/save",
                json={"name": "Acoustic"},
            )
            assert save_resp.status == 200
            save_data = await save_resp.json()
            assert save_data["success"] is True

            # 3. Get Presets
            get_resp = await client.get("/api/v1/guild/111/presets")
            assert get_resp.status == 200
            get_data = await get_resp.json()
            assert "acoustic" in get_data["presets"]

            # 4. Load Preset
            load_resp = await client.post(
                "/api/v1/guild/111/presets/load",
                json={"name": "acoustic"},
            )
            assert load_resp.status == 200

            # 5. Delete Preset
            del_resp = await client.delete("/api/v1/guild/111/presets/acoustic")
            assert del_resp.status == 200

    @pytest.mark.asyncio
    async def test_websocket_nextgen_actions_dispatch(self, app):
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/ws/dashboard?guild_id=111")

            init_msg = await ws.receive_json()
            assert init_msg["type"] == "init"

            async def recv_type(expected):
                while True:
                    msg = await ws.receive_json()
                    if msg.get("type") == expected:
                        return msg

            # 1. WS set_equalizer
            await ws.send_json({"action": "set_equalizer", "preset": "electronic"})
            res1 = await recv_type("action_result")
            assert res1["action"] == "set_equalizer"
            assert res1["result"]["success"] is True

            # 2. WS save_preset
            await ws.send_json({"action": "save_preset", "name": "BassVibe"})
            res2 = await recv_type("action_result")
            assert res2["action"] == "save_preset"
            assert res2["result"]["success"] is True

            # 3. WS get_presets
            await ws.send_json({"action": "get_presets"})
            res3 = await recv_type("presets_list")
            assert "bassvibe" in res3["data"]["presets"]

            # 4. WS load_preset
            await ws.send_json({"action": "load_preset", "name": "bassvibe"})
            res4 = await recv_type("action_result")
            assert res4["action"] == "load_preset"
            assert res4["result"]["success"] is True

            # 5. WS get_lyrics
            with patch("lyrics.service.LyricsService.get_lyrics", AsyncMock(return_value=([], False))):
                await ws.send_json({"action": "get_lyrics"})
                res5 = await recv_type("lyrics")
                assert res5["type"] == "lyrics"

            await ws.close()

    def test_offline_canvas_spectrum_and_file_separation(self):
        base_dir = Path(__file__).parent.parent / "dashboard"

        # Verify separate frontend files exist
        assert (base_dir / "static" / "css" / "dashboard.css").exists()
        assert (base_dir / "static" / "js" / "visualizer.js").exists()
        assert (base_dir / "static" / "js" / "equalizer.js").exists()
        assert (base_dir / "static" / "js" / "app.js").exists()
        assert (base_dir / "templates" / "index.html").exists()

        # Verify visualizer uses pure Math.sin and canvas
        vis_code = (base_dir / "static" / "js" / "visualizer.js").read_text(encoding="utf-8")
        assert "Math.sin" in vis_code
        assert "requestAnimationFrame" in vis_code
        assert "AudioSpectrumVisualizer" in vis_code

        # Verify equalizer script has 4 bands and LED support
        eq_code = (base_dir / "static" / "js" / "equalizer.js").read_text(encoding="utf-8")
        assert "sub_bass" in eq_code
        assert "savePresetToMySQL" in eq_code
        assert "led-indicator" in eq_code

        # Verify HTML template contains canvas and zero external CDNs
        html = render_dashboard_html()
        assert "audio-spectrum-canvas" in html
        assert "4-Band Graphic Equalizer" in html
        assert "18 Audio Effects Toggle Board" in html
        assert "Synced Karaoke Lyrics Teleprompter" in html
        assert "fonts.googleapis.com" not in html
        assert "cdnjs.cloudflare.com" not in html
        assert "cdn.jsdelivr.net" not in html

    @pytest.mark.asyncio
    async def test_dashboard_enqueue_triggers_clean_board(self, mock_bot, dummy_track):
        svc = DashboardService(mock_bot)
        mock_bot.update_clean_board = AsyncMock()
        def _safe_track(task_or_coro, *a, **k):
            import asyncio
            if asyncio.iscoroutine(task_or_coro):
                task_or_coro.close()
            return MagicMock()
        mock_bot.track_task = MagicMock(side_effect=_safe_track)

        res = await svc.add_to_queue(111, "https://youtube.com/watch?v=12345", requested_by="WebAdmin")
        assert res["success"] is True
        mock_bot.track_task.assert_called()
        mock_bot.update_clean_board.assert_called_with(111)

    @pytest.mark.asyncio
    async def test_dashboard_ws_sync_loop_resilience(self, mock_bot):
        mgr = DashboardWebSocketManager(DashboardService(mock_bot))
        mock_ws = MagicMock()
        mgr._clients.add(mock_ws)
        mgr._client_guilds[mock_ws] = 111

        iteration_count = 0

        def faulty_get_state(gid):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count == 1:
                raise RuntimeError("Simulated transient socket glitch")
            return {"is_playing": False}

        mgr.service.get_guild_state = MagicMock(side_effect=faulty_get_state)

        with patch("asyncio.sleep", AsyncMock(side_effect=[None, None, asyncio.CancelledError()])):
            # Run the loop until CancelledError
            await mgr._sync_loop()

        # Should have survived the first exception and reached iteration 2
        assert iteration_count >= 2

    @pytest.mark.asyncio
    async def test_dashboard_volume_and_loop_trigger_clean_board(self, mock_bot):
        svc = DashboardService(mock_bot)
        mock_bot.update_clean_board = AsyncMock()
        def _safe_track(task_or_coro, *a, **k):
            import asyncio
            if asyncio.iscoroutine(task_or_coro):
                task_or_coro.close()
            return MagicMock()
        mock_bot.track_task = MagicMock(side_effect=_safe_track)

        # Set volume triggers board update
        await svc.set_volume(111, 1.5)
        mock_bot.update_clean_board.assert_called_with(111)

        # Set loop mode triggers board update
        mock_bot.update_clean_board.reset_mock()
        await svc.set_loop_mode(111, "track")
        mock_bot.update_clean_board.assert_called_with(111)

    @pytest.mark.asyncio
    async def test_dashboard_skip_unregisters_radio_watchdog_and_queue_cancels_prefetch(self, mock_bot, dummy_track):
        svc = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = dummy_track
        player.is_radio_mode = True
        mock_bot.radio_watchdog = MagicMock()

        res = await svc.skip(111)
        assert res["success"] is True
        assert player.is_radio_mode is False
        mock_bot.radio_watchdog.unregister.assert_called_once_with(111)

        # Queue head mutation cancels prefetch
        t0 = Track(title="Song 0", url="https://ex.com/0", duration=100)
        t1 = Track(title="Song 1", url="https://ex.com/1", duration=100)
        await player.extend([t0, t1])
        player.cancel_prefetch = MagicMock()

        await svc.move_queue(111, from_index=0, to_index=1)
        player.cancel_prefetch.assert_called_once()

        player.cancel_prefetch.reset_mock()
        await svc.remove_from_queue(111, index=0)
        player.cancel_prefetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase6_ws_null_numeric_safety_and_multisource_search(self, mock_bot, dummy_track):
        svc = DashboardService(mock_bot)
        ws_mgr = DashboardWebSocketManager(svc)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        # Sending explicit None for numeric fields in WS messages must not raise TypeError/ValueError
        await ws_mgr._handle_client_message(ws, {"action": "volume", "guild_id": "111", "volume": None})
        await ws_mgr._handle_client_message(ws, {"action": "seek", "guild_id": "111", "position": "invalid"})
        await ws_mgr._handle_client_message(ws, {"action": "move_queue", "guild_id": "111", "from_index": None, "to_index": None})
        await ws_mgr._handle_client_message(ws, {"action": "remove_queue", "guild_id": "111", "index": None})
        await ws_mgr._handle_client_message(ws, {"action": "search", "guild_id": "111", "query": "lofi", "limit": None})

        # Multi-source search routing in DashboardService.search_tracks
        router = MagicMock()
        router.is_supported.return_value = True
        router.resolve = AsyncMock(return_value=[dummy_track])
        mock_bot.sources_router = router

        results = await svc.search_tracks("scsearch:lofi beats", limit=5)
        assert len(results) == 1
        assert results[0]["title"] == dummy_track.title
        router.resolve.assert_awaited_once_with("scsearch:lofi beats", max_tracks=5)

    @pytest.mark.asyncio
    async def test_phase7_dashboard_get_lyrics_non_youtube_guard(self, mock_bot):
        svc = DashboardService(mock_bot)
        player = mock_bot.get_player(111)
        player.now_playing = Track(
            title="Live Radio Stream",
            url="https://stream.example.com/live.mp3",
            duration=0,
        )
        res = await svc.get_lyrics(111)
        assert res["success"] is False
        assert "YouTube" in res["error"]


