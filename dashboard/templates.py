# -*- coding: utf-8 -*-
"""
dashboard/templates.py — Embedded offline-ready HTML5/CSS3/Vanilla JS template.

Features:
  - 100% self-contained: NO external CDN or font dependencies (system fonts & embedded SVGs)
  - Interactive Web Player: Play/Pause toggle, Skip, Seekbar with drag/click, Volume slider (0-200%)
  - Drag-and-Drop Queue Management: HTML5 Drag & Drop API with visual indicators & instant reordering
  - Real-time Local WebSocket client with auto-reconnect & client-side elapsed interpolation
  - Modern Glassmorphic Dark UI design with responsive layout & floating toast notifications
"""

from __future__ import annotations


def render_dashboard_html(port: int = 8080, api_secret: str = "") -> str:
    """Render the single-page dashboard HTML string."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Music Bot V3 — Local Dashboard</title>
  <style>
    :root {{
      --bg: #0b0f17;
      --bg-card: rgba(22, 28, 40, 0.85);
      --bg-card-hover: rgba(28, 36, 52, 0.95);
      --border: rgba(255, 255, 255, 0.08);
      --border-focus: rgba(124, 92, 191, 0.6);
      --accent: #7c5cbf;
      --accent-gradient: linear-gradient(135deg, #7c5cbf 0%, #5865f2 50%, #00b4d8 100%);
      --accent-light: #9d77e8;
      --green: #10b981;
      --red: #ef4444;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 18px;
      --shadow-sm: 0 4px 12px rgba(0, 0, 0, 0.3);
      --shadow-md: 0 8px 30px rgba(0, 0, 0, 0.45);
      --shadow-glow: 0 0 24px rgba(124, 92, 191, 0.35);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }}

    /* Top Navbar */
    .navbar {{
      background: rgba(15, 21, 32, 0.9);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 0.85rem 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.25rem;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      text-decoration: none;
      color: inherit;
    }}
    .brand-icon {{
      width: 36px;
      height: 36px;
      border-radius: var(--radius-sm);
      background: var(--accent-gradient);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 10px rgba(124, 92, 191, 0.4);
    }}
    .brand h1 {{
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      background: linear-gradient(135deg, #fff 30%, #cbd5e1 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .brand-sub {{
      font-size: 0.75rem;
      color: var(--text-muted);
      font-weight: 400;
    }}

    .nav-controls {{
      display: flex;
      align-items: center;
      gap: 1rem;
    }}
    .guild-select-wrapper {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.35rem 0.75rem;
    }}
    .guild-select-wrapper label {{
      font-size: 0.8rem;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }}
    select.guild-select {{
      background: transparent;
      border: none;
      color: var(--text);
      font-size: 0.85rem;
      font-weight: 600;
      outline: none;
      cursor: pointer;
      padding-right: 0.5rem;
    }}
    select.guild-select option {{
      background: #161c28;
      color: var(--text);
    }}

    .ws-indicator {{
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.8rem;
      padding: 0.35rem 0.75rem;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      border-radius: 99px;
      font-weight: 500;
    }}
    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--text-dim);
      transition: background 0.3s, box-shadow 0.3s;
    }}
    .status-dot.online {{
      background: var(--green);
      box-shadow: 0 0 10px var(--green);
      animation: pulseDot 2s infinite;
    }}
    .status-dot.reconnecting {{
      background: #f59e0b;
      box-shadow: 0 0 8px #f59e0b;
    }}
    .status-dot.offline {{
      background: var(--red);
    }}
    @keyframes pulseDot {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.6; transform: scale(0.9); }}
    }}

    /* Main Container */
    .container {{
      max-width: 1400px;
      margin: 1.5rem auto;
      padding: 0 1.5rem;
      width: 100%;
      flex: 1;
    }}

    .dashboard-grid {{
      display: grid;
      grid-template-columns: 460px 1fr;
      gap: 1.75rem;
      align-items: start;
    }}
    @media (max-width: 1024px) {{
      .dashboard-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    /* Glass Cards */
    .card {{
      background: var(--bg-card);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 1.75rem;
      box-shadow: var(--shadow-md);
      transition: border-color 0.2s;
    }}
    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1.25rem;
    }}
    .card-title {{
      font-size: 0.95rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}

    /* Left Column: Player Card */
    .player-card {{
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      position: sticky;
      top: 5rem;
    }}
    .art-container {{
      width: 100%;
      max-width: 340px;
      aspect-ratio: 16/9;
      border-radius: var(--radius-md);
      overflow: hidden;
      margin-bottom: 1.25rem;
      background: #1a2232;
      box-shadow: var(--shadow-sm);
      position: relative;
      border: 1px solid var(--border);
    }}
    .art-container img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
      transition: transform 0.4s ease;
    }}
    .art-placeholder {{
      width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: var(--text-dim);
      background: linear-gradient(135deg, #131926 0%, #1e2638 100%);
    }}
    .art-placeholder svg {{
      width: 56px;
      height: 56px;
      margin-bottom: 0.5rem;
      stroke: var(--text-dim);
    }}

    .track-meta {{
      width: 100%;
      margin-bottom: 1.25rem;
    }}
    .track-title {{
      font-size: 1.25rem;
      font-weight: 700;
      line-height: 1.35;
      color: var(--text);
      margin-bottom: 0.35rem;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      word-break: break-word;
    }}
    .track-author {{
      font-size: 0.9rem;
      color: var(--accent-light);
      font-weight: 500;
      margin-bottom: 0.25rem;
    }}
    .track-requester {{
      font-size: 0.75rem;
      color: var(--text-dim);
    }}

    /* Progress & Scrubber */
    .scrubber-container {{
      width: 100%;
      margin-bottom: 1.5rem;
    }}
    .scrubber-times {{
      display: flex;
      justify-content: space-between;
      font-size: 0.75rem;
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      margin-bottom: 0.4rem;
    }}
    .scrubber-bar {{
      width: 100%;
      height: 8px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 99px;
      position: relative;
      cursor: pointer;
      overflow: hidden;
      transition: height 0.15s ease;
    }}
    .scrubber-bar:hover {{
      height: 10px;
    }}
    .scrubber-progress {{
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 0%;
      background: var(--accent-gradient);
      border-radius: 99px;
      transition: width 0.15s linear;
    }}

    /* Controls */
    .player-controls {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 1.25rem;
      width: 100%;
      margin-bottom: 1.5rem;
    }}
    .btn-circle {{
      border: none;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.2s, box-shadow 0.2s;
      outline: none;
    }}
    .btn-circle:active {{
      transform: scale(0.92);
    }}
    .btn-play {{
      width: 60px;
      height: 60px;
      background: var(--accent-gradient);
      color: #fff;
      box-shadow: 0 4px 18px rgba(124, 92, 191, 0.5);
    }}
    .btn-play:hover {{
      transform: scale(1.06);
      box-shadow: 0 6px 24px rgba(124, 92, 191, 0.7);
    }}
    .btn-sec {{
      width: 44px;
      height: 44px;
      background: rgba(255, 255, 255, 0.06);
      color: var(--text);
      border: 1px solid var(--border);
    }}
    .btn-sec:hover {{
      background: rgba(255, 255, 255, 0.12);
      color: #fff;
      transform: scale(1.05);
    }}

    /* Volume Slider */
    .volume-box {{
      width: 100%;
      display: flex;
      align-items: center;
      gap: 0.85rem;
      padding: 0.75rem 1rem;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
    }}
    .volume-box svg {{
      width: 20px;
      height: 20px;
      stroke: var(--text-muted);
      flex-shrink: 0;
    }}
    .volume-slider {{
      flex: 1;
      -webkit-appearance: none;
      appearance: none;
      height: 6px;
      border-radius: 99px;
      background: rgba(255, 255, 255, 0.1);
      outline: none;
      cursor: pointer;
    }}
    .volume-slider::-webkit-slider-thumb {{
      -webkit-appearance: none;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
      cursor: pointer;
      transition: transform 0.15s;
    }}
    .volume-slider::-webkit-slider-thumb:hover {{
      transform: scale(1.2);
    }}
    .volume-value {{
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-muted);
      width: 42px;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

    /* Right Column: Queue */
    .queue-card {{
      min-height: 580px;
      display: flex;
      flex-direction: column;
    }}
    .queue-stats {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }}
    .queue-badge {{
      background: rgba(124, 92, 191, 0.2);
      color: var(--accent-light);
      border: 1px solid rgba(124, 92, 191, 0.3);
      border-radius: 99px;
      padding: 0.2rem 0.6rem;
      font-size: 0.75rem;
      font-weight: 700;
    }}
    .queue-actions {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .btn-action {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text-muted);
      padding: 0.35rem 0.7rem;
      font-size: 0.75rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 0.35rem;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .btn-action:hover {{
      background: rgba(255, 255, 255, 0.12);
      color: var(--text);
    }}

    /* Queue List & Drag and Drop */
    .queue-list {{
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      margin-top: 0.5rem;
      flex: 1;
      overflow-y: auto;
      max-height: 700px;
      padding-right: 0.25rem;
    }}
    .queue-list::-webkit-scrollbar {{
      width: 6px;
    }}
    .queue-list::-webkit-scrollbar-thumb {{
      background: rgba(255, 255, 255, 0.15);
      border-radius: 99px;
    }}

    .queue-item {{
      background: rgba(255, 255, 255, 0.025);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 0.65rem 0.85rem;
      display: flex;
      align-items: center;
      gap: 0.85rem;
      cursor: grab;
      user-select: none;
      transition: transform 0.15s ease, background 0.15s, border-color 0.15s, opacity 0.15s;
      position: relative;
    }}
    .queue-item:hover {{
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(255, 255, 255, 0.15);
    }}
    .queue-item:active {{
      cursor: grabbing;
    }}

    /* Dragging States */
    .queue-item.dragging {{
      opacity: 0.45;
      background: rgba(124, 92, 191, 0.15);
      border: 1px dashed var(--accent-light);
      transform: scale(0.98);
    }}
    .queue-item.drop-target-above {{
      border-top: 2px solid var(--accent-light);
    }}
    .queue-item.drop-target-below {{
      border-bottom: 2px solid var(--accent-light);
    }}

    .item-drag-handle {{
      color: var(--text-dim);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: grab;
      padding: 0.2rem;
    }}
    .item-index {{
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-dim);
      width: 22px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }}
    .item-thumb {{
      width: 44px;
      height: 44px;
      border-radius: var(--radius-sm);
      object-fit: cover;
      background: #1c2434;
      flex-shrink: 0;
    }}
    .item-info {{
      flex: 1;
      min-width: 0;
    }}
    .item-title {{
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .item-sub {{
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-top: 0.15rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .item-dur {{
      font-size: 0.8rem;
      color: var(--text-muted);
      font-weight: 500;
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
    }}
    .item-remove {{
      background: transparent;
      border: none;
      color: var(--text-dim);
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.15s, background 0.15s, color 0.15s;
    }}
    .queue-item:hover .item-remove {{
      opacity: 1;
    }}
    .item-remove:hover {{
      background: rgba(239, 68, 68, 0.15);
      color: var(--red);
    }}

    .empty-state {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 3rem 1rem;
      color: var(--text-dim);
      flex: 1;
    }}
    .empty-state svg {{
      width: 64px;
      height: 64px;
      stroke: var(--text-dim);
      margin-bottom: 1rem;
      opacity: 0.5;
    }}
    .empty-state h3 {{
      font-size: 1.1rem;
      color: var(--text-muted);
      margin-bottom: 0.35rem;
    }}
    .empty-state p {{
      font-size: 0.85rem;
      max-width: 280px;
    }}

    /* Toast Notifications */
    .toast-container {{
      position: fixed;
      bottom: 1.5rem;
      right: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      z-index: 1000;
      pointer-events: none;
    }}
    .toast {{
      background: #1e2638;
      color: #fff;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.65rem 1rem;
      font-size: 0.85rem;
      box-shadow: var(--shadow-md);
      display: flex;
      align-items: center;
      gap: 0.5rem;
      pointer-events: auto;
      transform: translateY(20px);
      opacity: 0;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .toast.show {{
      transform: translateY(0);
      opacity: 1;
    }}
    .toast.success {{ border-left: 3px solid var(--green); }}
    .toast.error   {{ border-left: 3px solid var(--red); }}
    .toast.info    {{ border-left: 3px solid var(--accent); }}

    /* Footer */
    .footer {{
      text-align: center;
      padding: 1.5rem;
      font-size: 0.75rem;
      color: var(--text-dim);
      border-top: 1px solid var(--border);
      margin-top: 2rem;
    }}
  </style>
</head>
<body>

  <!-- Top Navigation -->
  <header class="navbar">
    <a href="/" class="brand">
      <div class="brand-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 18V5l12-2v13"></path>
          <circle cx="6" cy="18" r="3"></circle>
          <circle cx="18" cy="16" r="3"></circle>
        </svg>
      </div>
      <div>
        <h1>Music Bot V3</h1>
        <div class="brand-sub">Local Web Dashboard</div>
      </div>
    </a>

    <div class="nav-controls">
      <div class="guild-select-wrapper">
        <label for="guild-select">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
            <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
            <line x1="6" y1="6" x2="6.01" y2="6"></line>
            <line x1="6" y1="18" x2="6.01" y2="18"></line>
          </svg>
          Server:
        </label>
        <select id="guild-select" class="guild-select">
          <option value="">Loading servers…</option>
        </select>
      </div>

      <div class="ws-indicator" id="ws-indicator">
        <div class="status-dot" id="status-dot"></div>
        <span id="ws-status-text">Connecting…</span>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="container">
    <div class="dashboard-grid">

      <!-- Left Column: Interactive Web Player -->
      <section class="card player-card">
        <div class="card-header" style="width:100%">
          <div class="card-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
            </svg>
            Interactive Player
          </div>
          <span id="voice-channel-badge" style="font-size:0.75rem;color:var(--text-dim)">Voice: Disconnected</span>
        </div>

        <!-- Album Art / Thumbnail -->
        <div class="art-container" id="art-container">
          <div class="art-placeholder" id="art-placeholder">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="12" cy="12" r="10"></circle>
              <circle cx="12" cy="12" r="3"></circle>
            </svg>
            <span>No Track Playing</span>
          </div>
          <img id="track-art" src="" alt="Album Artwork" style="display:none" />
        </div>

        <!-- Track Metadata -->
        <div class="track-meta">
          <h2 class="track-title" id="track-title">Playback Idle</h2>
          <div class="track-author" id="track-author">—</div>
          <div class="track-requester" id="track-requester">Use Discord or web to queue songs</div>
        </div>

        <!-- Scrubber Bar -->
        <div class="scrubber-container">
          <div class="scrubber-times">
            <span id="time-elapsed">0:00</span>
            <span id="time-total">0:00</span>
          </div>
          <div class="scrubber-bar" id="scrubber-bar">
            <div class="scrubber-progress" id="scrubber-progress"></div>
          </div>
        </div>

        <!-- Playback Controls -->
        <div class="player-controls">
          <!-- Play / Pause Button -->
          <button class="btn-circle btn-play" id="btn-play-pause" title="Play / Pause">
            <svg id="icon-play" width="26" height="26" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 3 19 12 5 21 5 3"></polygon>
            </svg>
            <svg id="icon-pause" width="26" height="26" viewBox="0 0 24 24" fill="currentColor" style="display:none">
              <rect x="6" y="4" width="4" height="16"></rect>
              <rect x="14" y="4" width="4" height="16"></rect>
            </svg>
          </button>

          <!-- Skip Button -->
          <button class="btn-circle btn-sec" id="btn-skip" title="Skip Track">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 4 15 12 5 20 5 4"></polygon>
              <line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" stroke-width="2.5"></line>
            </svg>
          </button>
        </div>

        <!-- Volume Slider -->
        <div class="volume-box">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
          </svg>
          <input type="range" class="volume-slider" id="volume-slider" min="0" max="200" value="100" />
          <span class="volume-value" id="volume-text">100%</span>
        </div>
      </section>

      <!-- Right Column: Drag-and-Drop Queue Management -->
      <section class="card queue-card">
        <div class="card-header">
          <div class="queue-stats">
            <div class="card-title">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="8" y1="6" x2="21" y2="6"></line>
                <line x1="8" y1="12" x2="21" y2="12"></line>
                <line x1="8" y1="18" x2="21" y2="18"></line>
                <line x1="3" y1="6" x2="3.01" y2="6"></line>
                <line x1="3" y1="12" x2="3.01" y2="12"></line>
                <line x1="3" y1="18" x2="3.01" y2="18"></line>
              </svg>
              Queue Management
            </div>
            <span class="queue-badge" id="queue-count">0 tracks</span>
          </div>

          <div class="queue-actions">
            <button class="btn-action" id="btn-shuffle" title="Shuffle Queue">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="16 3 21 3 21 8"></polyline>
                <line x1="4" y1="20" x2="21" y2="3"></line>
                <polyline points="21 16 21 21 16 21"></polyline>
                <line x1="15" y1="15" x2="21" y2="21"></line>
                <line x1="4" y1="4" x2="9" y2="9"></line>
              </svg>
              Shuffle
            </button>
            <button class="btn-action" id="btn-clear" title="Clear Queue">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
              Clear
            </button>
          </div>
        </div>

        <!-- Draggable Queue List -->
        <ul class="queue-list" id="queue-list">
          <div class="empty-state" id="queue-empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M9 18V5l12-2v13"></path>
              <circle cx="6" cy="18" r="3"></circle>
              <circle cx="18" cy="16" r="3"></circle>
            </svg>
            <h3>Queue is Empty</h3>
            <p>Queue tracks using Discord or drag songs to reorder when available.</p>
          </div>
        </ul>
      </section>

    </div>
  </main>

  <!-- Toast Notification Container -->
  <div class="toast-container" id="toast-container"></div>

  <!-- Footer -->
  <footer class="footer">
    Music Bot V3 · Local In-Memory & WebSocket Dashboard · Offline First
  </footer>

  <!-- Vanilla JS Application -->
  <script>
    (function () {{
      'use strict';

      // ── State ──────────────────────────────────────────────────────────────
      const apiSecret = "{api_secret}";
      let currentGuildId = null;
      let currentState = null;
      let isPaused = true;
      let elapsedSeconds = 0;
      let totalDuration = 0;
      let timerInterval = null;
      let ws = null;
      let draggedItemIndex = null;

      function apiFetch(endpoint, options = {{}}) {{
        options.headers = options.headers || {{}};
        if (apiSecret) {{
          options.headers['Authorization'] = 'Bearer ' + apiSecret;
        }}
        return fetch(endpoint, options);
      }}

      // ── DOM References ─────────────────────────────────────────────────────
      const guildSelect     = document.getElementById('guild-select');
      const wsStatusText    = document.getElementById('ws-status-text');
      const statusDot       = document.getElementById('status-dot');
      const voiceBadge      = document.getElementById('voice-channel-badge');

      const trackArt        = document.getElementById('track-art');
      const artPlaceholder  = document.getElementById('art-placeholder');
      const trackTitle      = document.getElementById('track-title');
      const trackAuthor     = document.getElementById('track-author');
      const trackRequester  = document.getElementById('track-requester');

      const timeElapsed     = document.getElementById('time-elapsed');
      const timeTotal       = document.getElementById('time-total');
      const scrubberBar     = document.getElementById('scrubber-bar');
      const scrubberProgress= document.getElementById('scrubber-progress');

      const btnPlayPause    = document.getElementById('btn-play-pause');
      const iconPlay        = document.getElementById('icon-play');
      const iconPause       = document.getElementById('icon-pause');
      const btnSkip         = document.getElementById('btn-skip');

      const volumeSlider    = document.getElementById('volume-slider');
      const volumeText      = document.getElementById('volume-text');

      const queueCount      = document.getElementById('queue-count');
      const queueList       = document.getElementById('queue-list');
      const queueEmptyState = document.getElementById('queue-empty-state');
      const btnShuffle      = document.getElementById('btn-shuffle');
      const btnClear        = document.getElementById('btn-clear');
      const toastContainer  = document.getElementById('toast-container');

      // ── Toast Utility ──────────────────────────────────────────────────────
      function showToast(msg, type = 'info') {{
        const toast = document.createElement('div');
        toast.className = `toast ${{type}}`;
        toast.textContent = msg;
        toastContainer.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {{
          toast.classList.remove('show');
          setTimeout(() => toast.remove(), 300);
        }}, 2800);
      }}

      // ── Time Formatting ────────────────────────────────────────────────────
      function formatDuration(sec) {{
        if (!sec || isNaN(sec)) return '0:00';
        sec = Math.max(0, Math.floor(sec));
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return `${{m}}:${{s < 10 ? '0' : ''}}${{s}}`;
      }}

      // ── Elapsed Interpolation Timer ────────────────────────────────────────
      function startElapsedTimer() {{
        stopElapsedTimer();
        timerInterval = setInterval(() => {{
          if (!isPaused && totalDuration > 0 && elapsedSeconds < totalDuration) {{
            elapsedSeconds += 1;
            updateScrubber();
          }}
        }}, 1000);
      }}

      function stopElapsedTimer() {{
        if (timerInterval) {{
          clearInterval(timerInterval);
          timerInterval = null;
        }}
      }}

      function updateScrubber() {{
        timeElapsed.textContent = formatDuration(elapsedSeconds);
        timeTotal.textContent   = formatDuration(totalDuration);
        if (totalDuration > 0) {{
          const pct = Math.min(100, Math.max(0, (elapsedSeconds / totalDuration) * 100));
          scrubberProgress.style.width = pct + '%';
        }} else {{
          scrubberProgress.style.width = '0%';
        }}
      }}

      // ── WebSocket Connection ───────────────────────────────────────────────
      function connectWS() {{
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const params = [];
        if (currentGuildId) params.push('guild_id=' + encodeURIComponent(currentGuildId));
        if (apiSecret) params.push('token=' + encodeURIComponent(apiSecret));
        const qStr = params.length > 0 ? '?' + params.join('&') : '';
        const url = `${{proto}}://${{location.host}}/ws/dashboard${{qStr}}`;

        ws = new WebSocket(url);

        ws.onopen = () => {{
          statusDot.className = 'status-dot online';
          wsStatusText.textContent = '⚡ Live';
        }};

        ws.onmessage = (event) => {{
          try {{
            const msg = JSON.parse(event.data);
            handleWSMessage(msg);
          }} catch (e) {{
            console.error('WS parse error:', e);
          }}
        }};

        ws.onclose = () => {{
          statusDot.className = 'status-dot reconnecting';
          wsStatusText.textContent = 'Reconnecting…';
          stopElapsedTimer();
          setTimeout(connectWS, 2500);
        }};

        ws.onerror = () => ws.close();
      }}

      function sendWS(action, payload = {{}}) {{
        if (ws && ws.readyState === WebSocket.OPEN) {{
          ws.send(JSON.stringify({{ action, guild_id: currentGuildId, ...payload }}));
          return true;
        }}
        return false;
      }}

      function handleWSMessage(msg) {{
        if (msg.type === 'init') {{
          renderGuildSelect(msg.guilds);
          if (msg.selected_guild) {{
            applyGuildState(msg.selected_guild);
          }}
        }} else if (msg.type === 'guild_state') {{
          if (!currentGuildId || String(msg.guild_id) === String(currentGuildId)) {{
            applyGuildState(msg.data);
          }}
        }} else if (msg.type === 'action_result') {{
          if (msg.result && !msg.result.success && msg.result.error) {{
            showToast(msg.result.error, 'error');
          }}
        }}
      }}

      // ── UI Updates ─────────────────────────────────────────────────────────
      function renderGuildSelect(guilds) {{
        if (!guilds || guilds.length === 0) {{
          guildSelect.innerHTML = '<option value="">No servers available</option>';
          return;
        }}

        guildSelect.innerHTML = guilds.map(g => {{
          const isSelected = String(g.id) === String(currentGuildId) ? 'selected' : '';
          const activeMark = g.active ? ' 🎵' : '';
          return `<option value="${{g.id}}" ${{isSelected}}>${{g.name}}${{activeMark}}</option>`;
        }}).join('');

        if (!currentGuildId && guilds.length > 0) {{
          currentGuildId = guilds[0].id;
        }}
      }}

      function applyGuildState(state) {{
        if (!state) return;
        currentState = state;
        currentGuildId = state.guild_id;

        // Sync dropdown
        if (guildSelect.value !== String(currentGuildId)) {{
          guildSelect.value = String(currentGuildId);
        }}

        // Voice connection
        if (state.voice_connected) {{
          voiceBadge.textContent = `Voice: ${{state.voice_channel || 'Connected'}}`;
          voiceBadge.style.color = 'var(--green)';
        }} else {{
          voiceBadge.textContent = 'Voice: Disconnected';
          voiceBadge.style.color = 'var(--text-dim)';
        }}

        // Now Playing
        const np = state.now_playing;
        if (np) {{
          trackTitle.textContent = np.title || 'Unknown Title';
          trackAuthor.textContent = np.uploader || 'Unknown Artist';
          trackRequester.textContent = np.requested_by ? `Requested by ${{np.requested_by}}` : '';

          if (np.thumbnail) {{
            trackArt.src = np.thumbnail;
            trackArt.style.display = 'block';
            artPlaceholder.style.display = 'none';
          }} else {{
            trackArt.style.display = 'none';
            artPlaceholder.style.display = 'flex';
          }}

          elapsedSeconds = np.elapsed || 0;
          totalDuration  = np.duration || 0;
          isPaused       = !!state.is_paused;

          if (state.is_playing && !isPaused) {{
            iconPlay.style.display  = 'none';
            iconPause.style.display = 'block';
            startElapsedTimer();
          }} else {{
            iconPlay.style.display  = 'block';
            iconPause.style.display = 'none';
            stopElapsedTimer();
          }}
        }} else {{
          trackTitle.textContent = 'Playback Idle';
          trackAuthor.textContent = '—';
          trackRequester.textContent = 'No track currently active';
          trackArt.style.display = 'none';
          artPlaceholder.style.display = 'flex';
          elapsedSeconds = 0;
          totalDuration = 0;
          isPaused = true;
          iconPlay.style.display  = 'block';
          iconPause.style.display = 'none';
          stopElapsedTimer();
        }}
        updateScrubber();

        // Volume
        const volPct = state.volume_percent ?? Math.round((state.volume || 1.0) * 100);
        volumeSlider.value = volPct;
        volumeText.textContent = `${{volPct}}%`;

        // Render Queue
        renderQueue(state.queue || []);
      }}

      // ── Queue Drag and Drop ────────────────────────────────────────────────
      function renderQueue(queue) {{
        queueCount.textContent = `${{queue.length}} track${{queue.length !== 1 ? 's' : ''}}`;

        if (queue.length === 0) {{
          queueList.innerHTML = '';
          queueList.appendChild(queueEmptyState);
          return;
        }}

        queueEmptyState.remove();
        queueList.innerHTML = '';

        queue.forEach((item, idx) => {{
          const li = document.createElement('li');
          li.className = 'queue-item';
          li.draggable = true;
          li.dataset.index = idx;

          li.innerHTML = `
            <div class="item-drag-handle" title="Drag to reorder">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="9" cy="6" r="2"></circle>
                <circle cx="15" cy="6" r="2"></circle>
                <circle cx="9" cy="12" r="2"></circle>
                <circle cx="15" cy="12" r="2"></circle>
                <circle cx="9" cy="18" r="2"></circle>
                <circle cx="15" cy="18" r="2"></circle>
              </svg>
            </div>
            <div class="item-index">${{idx + 1}}</div>
            <img class="item-thumb" src="${{item.thumbnail || ''}}" onerror="this.style.display='none'" alt="" />
            <div class="item-info">
              <div class="item-title" title="${{item.title}}">${{item.title}}</div>
              <div class="item-sub">
                <span>${{item.uploader || 'Unknown'}}</span>
                ${{item.requested_by ? `<span>• by ${{item.requested_by}}</span>` : ''}}
              </div>
            </div>
            <div class="item-dur">${{formatDuration(item.duration)}}</div>
            <button class="item-remove" title="Remove from queue" data-index="${{idx}}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          `;

          // HTML5 Drag Events
          li.addEventListener('dragstart', (e) => {{
            draggedItemIndex = idx;
            li.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', String(idx));
          }});

          li.addEventListener('dragend', () => {{
            li.classList.remove('dragging');
            clearDropIndicators();
            draggedItemIndex = null;
          }});

          li.addEventListener('dragover', (e) => {{
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            const rect = li.getBoundingClientRect();
            const relY = e.clientY - rect.top;
            clearDropIndicators();
            if (relY < rect.height / 2) {{
              li.classList.add('drop-target-above');
            }} else {{
              li.classList.add('drop-target-below');
            }}
          }});

          li.addEventListener('dragleave', () => {{
            li.classList.remove('drop-target-above', 'drop-target-below');
          }});

          li.addEventListener('drop', (e) => {{
            e.preventDefault();
            const rect = li.getBoundingClientRect();
            const relY = e.clientY - rect.top;
            let targetIdx = idx;
            if (relY >= rect.height / 2) {{
              targetIdx = idx; // drop after or same
            }}
            clearDropIndicators();

            if (draggedItemIndex !== null && draggedItemIndex !== targetIdx) {{
              handleQueueMove(draggedItemIndex, targetIdx);
            }}
          }});

          // Remove Button
          const removeBtn = li.querySelector('.item-remove');
          removeBtn.addEventListener('click', (e) => {{
            e.stopPropagation();
            handleQueueRemove(idx);
          }});

          queueList.appendChild(li);
        }});
      }}

      function clearDropIndicators() {{
        document.querySelectorAll('.queue-item').forEach(el => {{
          el.classList.remove('drop-target-above', 'drop-target-below');
        }});
      }}

      // ── Actions ────────────────────────────────────────────────────────────
      async function handlePlayPause() {{
        if (!currentGuildId) return;
        const action = isPaused ? 'resume' : 'pause';
        if (!sendWS('toggle_pause')) {{
          // Fallback to REST
          await apiFetch(`/api/v1/guild/${{currentGuildId}}/${{action}}`, {{ method: 'POST' }});
        }}
      }}

      async function handleSkip() {{
        if (!currentGuildId) return;
        if (!sendWS('skip')) {{
          await apiFetch(`/api/v1/guild/${{currentGuildId}}/skip`, {{ method: 'POST' }});
        }}
        showToast('Skipping track…', 'info');
      }}

      let volDebounce = null;
      function handleVolumeChange(e) {{
        const val = parseInt(e.target.value, 10);
        volumeText.textContent = `${{val}}%`;
        clearTimeout(volDebounce);
        volDebounce = setTimeout(() => {{
          if (!currentGuildId) return;
          if (!sendWS('volume', {{ volume: val }})) {{
            apiFetch(`/api/v1/guild/${{currentGuildId}}/volume`, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ volume: val }})
            }});
          }}
        }}, 60);
      }}

      function handleSeekClick(e) {{
        if (!currentGuildId || totalDuration <= 0) return;
        const rect = scrubberBar.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const pct = Math.max(0, Math.min(1, clickX / rect.width));
        const targetSec = Math.floor(pct * totalDuration);

        elapsedSeconds = targetSec;
        updateScrubber();

        if (!sendWS('seek', {{ position: targetSec }})) {{
          apiFetch(`/api/v1/guild/${{currentGuildId}}/seek`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ position: targetSec }})
          }});
        }}
        showToast(`Seek to ${{formatDuration(targetSec)}}`, 'info');
      }}

      async function handleQueueMove(fromIdx, toIdx) {{
        if (!currentGuildId) return;
        if (!sendWS('move_queue', {{ from_index: fromIdx, to_index: toIdx }})) {{
          await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/move`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ from_index: fromIdx, to_index: toIdx }})
          }});
        }}
        showToast(`Moved #${{fromIdx + 1}} to #${{toIdx + 1}}`, 'success');
      }}

      async function handleQueueRemove(index) {{
        if (!currentGuildId) return;
        if (!sendWS('remove_queue', {{ index }})) {{
          await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/${{index}}`, {{ method: 'DELETE' }});
        }}
        showToast(`Removed track #${{index + 1}}`, 'info');
      }}

      async function handleShuffle() {{
        if (!currentGuildId) return;
        if (!sendWS('shuffle_queue')) {{
          await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/shuffle`, {{ method: 'POST' }});
        }}
        showToast('Queue shuffled', 'success');
      }}

      async function handleClear() {{
        if (!currentGuildId) return;
        if (confirm('Are you sure you want to clear the entire queue?')) {{
          if (!sendWS('clear_queue')) {{
            await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/clear`, {{ method: 'POST' }});
          }}
          showToast('Queue cleared', 'info');
        }}
      }}

      // ── Event Listeners ────────────────────────────────────────────────────
      guildSelect.addEventListener('change', (e) => {{
        currentGuildId = e.target.value;
        sendWS('select_guild', {{ guild_id: currentGuildId }});
      }});

      btnPlayPause.addEventListener('click', handlePlayPause);
      btnSkip.addEventListener('click', handleSkip);
      volumeSlider.addEventListener('input', handleVolumeChange);
      scrubberBar.addEventListener('click', handleSeekClick);
      btnShuffle.addEventListener('click', handleShuffle);
      btnClear.addEventListener('click', handleClear);

      // Start Connection
      connectWS();

    }})();
  </script>
</body>
</html>"""
