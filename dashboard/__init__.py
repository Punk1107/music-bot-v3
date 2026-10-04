# -*- coding: utf-8 -*-
"""
dashboard/ — Local Web Dashboard module for Music Bot V3 (Group 4).

Features:
  - 4.1 Interactive Web Player (Pause, Resume, Skip, Volume, Seek, Local WebSocket)
  - 4.2 Drag-and-Drop Web Queue Management (HTML5 Drag and Drop API)
"""

from __future__ import annotations

from dashboard.cog import DashboardCog
from dashboard.routes import DashboardRouter
from dashboard.service import DashboardService
from dashboard.templates import render_dashboard_html
from dashboard.websocket import DashboardWebSocketManager

__all__ = [
    "DashboardCog",
    "DashboardRouter",
    "DashboardService",
    "DashboardWebSocketManager",
    "render_dashboard_html",
]
