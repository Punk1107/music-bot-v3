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

    /* Ambient Glow (dynamic via JS-driven CSS vars) */
    .art-glow {{
      position: absolute;
      inset: -30px;
      background: var(--glow-color, var(--accent));
      filter: blur(60px);
      opacity: 0;
      z-index: -1;
      border-radius: 50%;
      transition: opacity 0.6s ease, background 1.2s ease;
      pointer-events: none;
    }}
    .player-card:hover .art-glow,
    .player-card.playing .art-glow {{
      opacity: 0.18;
    }}

    /* Spinning art when playing */
    .art-container img.spinning {{
      animation: artSpin 20s linear infinite;
    }}
    @keyframes artSpin {{
      from {{ transform: rotate(0deg); }}
      to   {{ transform: rotate(360deg); }}
    }}
    .art-container img.spin-paused {{
      animation-play-state: paused;
    }}

    /* Mini scrubber knob */
    .scrubber-bar:hover .scrubber-progress::after {{
      content: '';
      position: absolute;
      right: -5px;
      top: 50%;
      transform: translateY(-50%);
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    }}

    /* Loop button active state */
    .btn-loop.active {{
      background: rgba(124, 92, 191, 0.3);
      border-color: var(--accent-light);
      color: var(--accent-light);
    }}

    /* Color Themes */
    [data-theme="emerald"] {{
      --accent: #1db954;
      --accent-light: #1ed760;
      --accent-gradient: linear-gradient(135deg, #1db954 0%, #10b981 100%);
      --border-focus: rgba(29, 185, 84, 0.6);
      --shadow-glow: 0 0 24px rgba(29, 185, 84, 0.35);
    }}
    [data-theme="blurple"] {{
      --accent: #5865f2;
      --accent-light: #7983f5;
      --accent-gradient: linear-gradient(135deg, #5865f2 0%, #3b82f6 100%);
      --border-focus: rgba(88, 101, 242, 0.6);
      --shadow-glow: 0 0 24px rgba(88, 101, 242, 0.35);
    }}
    [data-theme="cyan"] {{
      --accent: #00e5ff;
      --accent-light: #38bdf8;
      --accent-gradient: linear-gradient(135deg, #00e5ff 0%, #0284c7 100%);
      --border-focus: rgba(0, 229, 255, 0.6);
      --shadow-glow: 0 0 24px rgba(0, 229, 255, 0.35);
    }}
    [data-theme="rose"] {{
      --accent: #f43f5e;
      --accent-light: #fb7185;
      --accent-gradient: linear-gradient(135deg, #f43f5e 0%, #ec4899 100%);
      --border-focus: rgba(244, 63, 94, 0.6);
      --shadow-glow: 0 0 24px rgba(244, 63, 94, 0.35);
    }}

    /* Nav Buttons & Theme Menu */
    .btn-nav {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: var(--radius-sm);
      padding: 0.35rem 0.65rem;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
    }}
    .btn-nav:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: var(--text);
    }}
    .theme-select-wrapper {{
      position: relative;
    }}
    .theme-menu {{
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      background: rgba(20, 26, 38, 0.98);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-md);
      z-index: 110;
      padding: 0.35rem;
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      min-width: 160px;
    }}
    .theme-menu button {{
      background: transparent;
      border: none;
      color: var(--text);
      font-size: 0.8rem;
      padding: 0.4rem 0.6rem;
      text-align: left;
      border-radius: 4px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .theme-menu button:hover {{
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
    }}

    /* Soundwave Audio Indicator */
    .soundwave {{
      display: inline-flex;
      align-items: flex-end;
      gap: 3px;
      height: 16px;
      margin-left: 0.5rem;
      vertical-align: middle;
    }}
    .sw-bar {{
      width: 3px;
      background: var(--accent-light);
      border-radius: 2px;
      height: 4px;
      transition: height 0.2s ease;
    }}
    .player-card.playing .sw-bar {{
      animation: swBounce 1.2s ease-in-out infinite alternate;
    }}
    .player-card.playing .sw-bar:nth-child(1) {{ animation-delay: 0.1s; height: 10px; }}
    .player-card.playing .sw-bar:nth-child(2) {{ animation-delay: 0.3s; height: 16px; }}
    .player-card.playing .sw-bar:nth-child(3) {{ animation-delay: 0.2s; height: 7px; }}
    .player-card.playing .sw-bar:nth-child(4) {{ animation-delay: 0.4s; height: 14px; }}
    .player-card.playing .sw-bar:nth-child(5) {{ animation-delay: 0.15s; height: 9px; }}
    @keyframes swBounce {{
      0% {{ height: 3px; }}
      100% {{ height: 16px; }}
    }}

    /* Quick Audio Effects */
    .effects-section {{
      margin-top: 1rem;
      padding-top: 0.85rem;
      border-top: 1px solid var(--border);
      width: 100%;
    }}
    .effects-label {{
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 0.45rem;
      display: block;
    }}
    .effects-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }}
    .pill-btn {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: 99px;
      padding: 0.25rem 0.6rem;
      font-size: 0.75rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .pill-btn:hover {{
      background: rgba(255, 255, 255, 0.08);
      color: var(--text);
      border-color: rgba(255, 255, 255, 0.2);
    }}
    .pill-btn.active {{
      background: rgba(124, 92, 191, 0.25);
      border-color: var(--accent-light);
      color: #fff;
      box-shadow: 0 0 10px rgba(124, 92, 191, 0.3);
    }}

    /* Search Box & Results Dropdown */
    .search-box-wrapper {{
      position: relative;
      margin-bottom: 0.85rem;
    }}
    .search-input-group {{
      display: flex;
      align-items: center;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.4rem 0.75rem;
      gap: 0.5rem;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    .search-input-group:focus-within {{
      border-color: var(--border-focus);
      box-shadow: 0 0 0 2px rgba(124, 92, 191, 0.2);
    }}
    .search-input-group input {{
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text);
      font-size: 0.85rem;
      outline: none;
    }}
    .search-input-group input::placeholder {{
      color: var(--text-dim);
    }}
    .btn-search-go {{
      background: var(--accent);
      border: none;
      color: #fff;
      font-size: 0.75rem;
      font-weight: 600;
      border-radius: 4px;
      padding: 0.3rem 0.65rem;
      cursor: pointer;
      transition: opacity 0.15s;
    }}
    .btn-search-go:hover {{
      opacity: 0.85;
    }}
    .search-results-dropdown {{
      position: absolute;
      top: calc(100% + 6px);
      left: 0;
      right: 0;
      background: rgba(20, 26, 38, 0.98);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-md);
      z-index: 50;
      max-height: 320px;
      overflow-y: auto;
      padding: 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }}
    .search-result-item {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
      padding: 0.4rem 0.5rem;
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.02);
      transition: background 0.15s;
    }}
    .search-result-item:hover {{
      background: rgba(255, 255, 255, 0.07);
    }}
    .search-result-thumb {{
      width: 40px;
      height: 40px;
      border-radius: 4px;
      object-fit: cover;
      background: #1c2434;
      flex-shrink: 0;
    }}
    .search-result-info {{
      flex: 1;
      min-width: 0;
    }}
    .search-result-title {{
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .search-result-sub {{
      font-size: 0.72rem;
      color: var(--text-muted);
    }}
    .search-result-btns {{
      display: flex;
      gap: 0.35rem;
      flex-shrink: 0;
    }}
    .btn-mini-add {{
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 0.7rem;
      font-weight: 600;
      border-radius: 4px;
      padding: 0.25rem 0.5rem;
      cursor: pointer;
      transition: all 0.15s;
    }}
    .btn-mini-add:hover {{
      background: var(--accent);
      border-color: var(--accent-light);
    }}

    /* Queue Toolbar */
    .queue-toolbar {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }}
    .queue-filter-input {{
      flex: 1;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.35rem 0.65rem;
      font-size: 0.8rem;
      color: var(--text);
      outline: none;
    }}
    .queue-filter-input:focus {{
      border-color: var(--border-focus);
    }}
    .queue-total-dur {{
      font-size: 0.75rem;
      color: var(--text-dim);
      white-space: nowrap;
      font-weight: 600;
    }}
    .item-playnext {{
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 0.85rem;
      cursor: pointer;
      padding: 0.2rem 0.35rem;
      border-radius: 4px;
      opacity: 0;
      transition: opacity 0.15s, color 0.15s, background 0.15s;
    }}
    .queue-item:hover .item-playnext {{
      opacity: 1;
    }}
    .item-playnext:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: var(--accent-light);
    }}

    /* Modal / Drawer */
    .modal-overlay {{
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(8px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 200;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }}
    .modal-overlay.open {{
      opacity: 1;
      pointer-events: auto;
    }}
    .modal-box {{
      background: #182030;
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      box-shadow: var(--shadow-md);
      width: 90%;
      max-width: 480px;
      padding: 1.5rem;
      transform: scale(0.95);
      transition: transform 0.2s ease;
    }}
    .modal-overlay.open .modal-box {{
      transform: scale(1);
    }}
    .modal-title {{
      font-size: 1.1rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
    }}
    .modal-desc {{
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 1.25rem;
      line-height: 1.5;
    }}
    .modal-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
    }}
    .btn-modal {{
      padding: 0.45rem 1rem;
      font-size: 0.85rem;
      font-weight: 600;
      border-radius: var(--radius-sm);
      cursor: pointer;
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.05);
      color: var(--text);
    }}
    .btn-modal-danger {{
      background: var(--red);
      border-color: var(--red);
      color: #fff;
    }}

    /* Lyrics Drawer */
    .lyrics-drawer {{
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      width: 420px;
      max-width: 90vw;
      background: #141b27;
      border-left: 1px solid var(--border);
      box-shadow: -8px 0 30px rgba(0, 0, 0, 0.5);
      z-index: 150;
      transform: translateX(100%);
      transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      flex-direction: column;
    }}
    .lyrics-drawer.open {{
      transform: translateX(0);
    }}
    .lyrics-header {{
      padding: 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .lyrics-title {{
      font-size: 1rem;
      font-weight: 700;
    }}
    .lyrics-body {{
      flex: 1;
      overflow-y: auto;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.1rem;
    }}
    .lyric-line {{
      font-size: 0.95rem;
      line-height: 1.5;
      color: var(--text-dim);
      transition: all 0.2s ease;
      cursor: pointer;
      padding: 0.2rem 0;
    }}
    .lyric-line.active {{
      color: #fff;
      font-weight: 700;
      font-size: 1.1rem;
      transform: scale(1.02);
      color: var(--accent-light);
    }}
    .btn-close {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 1.2rem;
      cursor: pointer;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
    }}
    .btn-close:hover {{
      color: #fff;
      background: rgba(255,255,255,0.08);
    }}

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
      <!-- Theme Switcher -->
      <div class="theme-select-wrapper">
        <button class="btn-nav" id="btn-theme-toggle" title="Switch Theme Palette">🎨 Theme</button>
        <div class="theme-menu" id="theme-menu" style="display:none">
          <button data-theme="violet">🟣 Violet Nebula</button>
          <button data-theme="emerald">🟢 Spotify Emerald</button>
          <button data-theme="blurple">🔵 Discord Blurple</button>
          <button data-theme="cyan">🐬 Cyberpunk Cyan</button>
          <button data-theme="rose">🌸 Sunset Rose</button>
        </div>
      </div>

      <!-- Shortcuts button -->
      <button class="btn-nav" id="btn-shortcuts" title="Keyboard Shortcuts">⌨️ Keys</button>

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
            <div class="soundwave" id="soundwave" title="Audio Activity">
              <span class="sw-bar"></span>
              <span class="sw-bar"></span>
              <span class="sw-bar"></span>
              <span class="sw-bar"></span>
              <span class="sw-bar"></span>
            </div>
          </div>
          <span id="voice-channel-badge" style="font-size:0.75rem;color:var(--text-dim)">Voice: Disconnected</span>
        </div>

        <!-- Album Art / Thumbnail -->
        <div class="art-container" id="art-container">
          <div class="art-glow" id="art-glow"></div>
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
          <!-- Rewind 15s -->
          <button class="btn-circle btn-sec" id="btn-rewind" title="Rewind 15s">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="19 20 9 12 19 4 19 20"></polygon>
              <line x1="5" y1="19" x2="5" y2="5"></line>
            </svg>
          </button>

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
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="5 4 15 12 5 20 5 4"></polygon>
              <line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" stroke-width="2.5"></line>
            </svg>
          </button>

          <!-- Loop Button -->
          <button class="btn-circle btn-sec btn-loop" id="btn-loop" title="Toggle Loop">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="17 1 21 5 17 9"></polyline>
              <path d="M3 11V9a4 4 0 0 1 4-4h14"></path>
              <polyline points="7 23 3 19 7 15"></polyline>
              <path d="M21 13v2a4 4 0 0 1-4 4H3"></path>
            </svg>
          </button>

          <!-- Lyrics Toggle Button -->
          <button class="btn-circle btn-sec" id="btn-lyrics-toggle" title="View Live Lyrics">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
              <line x1="12" y1="19" x2="12" y2="22"></line>
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

        <!-- Quick Audio Effects Pills -->
        <div class="effects-section">
          <span class="effects-label">Audio Effects</span>
          <div class="effects-pills" id="effects-pills">
            <button class="pill-btn" data-effect="bass_boost">🎸 Bass Boost</button>
            <button class="pill-btn" data-effect="nightcore">🌙 Nightcore</button>
            <button class="pill-btn" data-effect="vaporwave">🌊 Vaporwave</button>
            <button class="pill-btn" data-effect="audio_8d">🔄 8D Audio</button>
            <button class="pill-btn" data-effect="treble_boost">⚡ Treble</button>
            <button class="pill-btn" data-effect="karaoke">🎤 Karaoke</button>
          </div>
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

        <!-- Search and Add Bar -->
        <div class="search-box-wrapper">
          <div class="search-input-group">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <input type="text" id="search-input" placeholder="Search track or paste YouTube/Spotify URL…" />
            <button class="btn-search-go" id="btn-search-go">Search</button>
          </div>
          <div class="search-results-dropdown" id="search-results-dropdown" style="display:none"></div>
        </div>

        <!-- Queue Toolbar: Filter + Total Duration -->
        <div class="queue-toolbar">
          <input type="text" class="queue-filter-input" id="queue-filter-input" placeholder="Filter queue tracks…" />
          <span class="queue-total-dur" id="queue-total-dur">Total: 0:00</span>
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
            <p>Queue tracks using Discord or search above to add songs.</p>
          </div>
        </ul>
      </section>

    </div>
  </main>

  <!-- Clear Queue Confirmation Modal -->
  <div class="modal-overlay" id="clear-modal">
    <div class="modal-box">
      <div class="modal-title">Clear Queue?</div>
      <div class="modal-desc">This will remove all tracks currently waiting in the queue. This action cannot be undone.</div>
      <div class="modal-actions">
        <button class="btn-modal" id="btn-cancel-clear">Cancel</button>
        <button class="btn-modal btn-modal-danger" id="btn-confirm-clear">Clear Queue</button>
      </div>
    </div>
  </div>

  <!-- Keyboard Shortcuts Modal -->
  <div class="modal-overlay" id="shortcuts-modal">
    <div class="modal-box">
      <div class="modal-title">⌨️ Keyboard Shortcuts</div>
      <div class="modal-desc">
        <table style="width:100%; border-collapse:collapse; font-size:0.85rem">
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>Space</strong></td><td style="color:var(--text-muted)">Play / Pause toggle</td></tr>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>Arrow Left / Right</strong></td><td style="color:var(--text-muted)">Rewind / Forward 15s</td></tr>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>Arrow Up / Down</strong></td><td style="color:var(--text-muted)">Volume +5% / -5%</td></tr>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>N</strong></td><td style="color:var(--text-muted)">Skip to next track</td></tr>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>L</strong></td><td style="color:var(--text-muted)">Cycle loop mode (Off/Track/Queue)</td></tr>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06)"><td style="padding:0.4rem 0"><strong>M</strong></td><td style="color:var(--text-muted)">Mute / Unmute</td></tr>
          <tr><td style="padding:0.4rem 0"><strong>?</strong></td><td style="color:var(--text-muted)">Toggle this help modal</td></tr>
        </table>
      </div>
      <div class="modal-actions">
        <button class="btn-modal" id="btn-close-shortcuts">Close</button>
      </div>
    </div>
  </div>

  <!-- Live Lyrics Drawer -->
  <div class="lyrics-drawer" id="lyrics-drawer">
    <div class="lyrics-header">
      <div>
        <div class="lyrics-title">Live Lyrics</div>
        <div style="font-size:0.75rem; color:var(--text-muted)" id="lyrics-track-title">—</div>
      </div>
      <button class="btn-close" id="btn-lyrics-close">✕</button>
    </div>
    <div class="lyrics-body" id="lyrics-content">
      <div style="text-align:center; color:var(--text-dim); margin-top:3rem;">No track playing or loading lyrics…</div>
    </div>
  </div>

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
        options.heade      // ── DOM References ─────────────────────────────────────────────────────
      const guildSelect     = document.getElementById('guild-select');
      const wsStatusText    = document.getElementById('ws-status-text');
      const statusDot       = document.getElementById('status-dot');
      const voiceBadge      = document.getElementById('voice-channel-badge');

      const btnThemeToggle  = document.getElementById('btn-theme-toggle');
      const themeMenu       = document.getElementById('theme-menu');
      const btnShortcuts    = document.getElementById('btn-shortcuts');
      const shortcutsModal  = document.getElementById('shortcuts-modal');
      const btnCloseShortcuts = document.getElementById('btn-close-shortcuts');
      const clearModal      = document.getElementById('clear-modal');
      const btnCancelClear  = document.getElementById('btn-cancel-clear');
      const btnConfirmClear = document.getElementById('btn-confirm-clear');

      const btnLyricsToggle = document.getElementById('btn-lyrics-toggle');
      const lyricsDrawer    = document.getElementById('lyrics-drawer');
      const btnLyricsClose  = document.getElementById('btn-lyrics-close');
      const lyricsTrackTitle= document.getElementById('lyrics-track-title');
      const lyricsContent   = document.getElementById('lyrics-content');

      const searchInput     = document.getElementById('search-input');
      const btnSearchGo     = document.getElementById('btn-search-go');
      const searchDropdown  = document.getElementById('search-results-dropdown');
      const queueFilterInput= document.getElementById('queue-filter-input');
      const queueTotalDur   = document.getElementById('queue-total-dur');

      const trackArt        = document.getElementById('track-art');
      const artPlaceholder  = document.getElementById('art-placeholder');
      const artGlow         = document.getElementById('art-glow');
      const trackTitle      = document.getElementById('track-title');
      const trackAuthor     = document.getElementById('track-author');
      const trackRequester  = document.getElementById('track-requester');
      const playerCard      = document.querySelector('.player-card');

      const timeElapsed     = document.getElementById('time-elapsed');
      const timeTotal       = document.getElementById('time-total');
      const scrubberBar     = document.getElementById('scrubber-bar');
      const scrubberProgress= document.getElementById('scrubber-progress');

      const btnPlayPause    = document.getElementById('btn-play-pause');
      const iconPlay        = document.getElementById('icon-play');
      const iconPause       = document.getElementById('icon-pause');
      const btnSkip         = document.getElementById('btn-skip');
      const btnRewind       = document.getElementById('btn-rewind');
      const btnLoop         = document.getElementById('btn-loop');

      const volumeSlider    = document.getElementById('volume-slider');
      const volumeText      = document.getElementById('volume-text');

      const queueCount      = document.getElementById('queue-count');
      const queueList       = document.getElementById('queue-list');
      const queueEmptyState = document.getElementById('queue-empty-state');
      const btnShuffle      = document.getElementById('btn-shuffle');
      const btnClear        = document.getElementById('btn-clear');
      const toastContainer  = document.getElementById('toast-container');

      let loopMode = 'off'; // 'off' | 'track' | 'queue'
      let rawQueue = [];
      let currentLyrics = null;
      let lastScrolledLyric = null;
      let isMuted = false;
      let prevVolume = 100;

      // ── Themes Management ──────────────────────────────────────────────────
      function applyTheme(name) {{
        document.documentElement.setAttribute('data-theme', name);
        try {{
          localStorage.setItem('music_bot_theme', name);
        }} catch (e) {{}}
      }}
      applyTheme(localStorage.getItem('music_bot_theme') || 'violet');

      if (btnThemeToggle) {{
        btnThemeToggle.addEventListener('click', (e) => {{
          e.stopPropagation();
          themeMenu.style.display = themeMenu.style.display === 'none' ? 'flex' : 'none';
        }});
      }}
      document.addEventListener('click', () => {{
        if (themeMenu) themeMenu.style.display = 'none';
        if (searchDropdown) searchDropdown.style.display = 'none';
      }});
      if (themeMenu) {{
        themeMenu.querySelectorAll('button').forEach(btn => {{
          btn.addEventListener('click', (e) => {{
            e.stopPropagation();
            applyTheme(btn.dataset.theme);
            themeMenu.style.display = 'none';
            showToast(`Theme: ${{btn.textContent.trim()}}`, 'info');
          }});
        }});
      }}

      // ── Shortcuts Modal ────────────────────────────────────────────────────
      if (btnShortcuts && shortcutsModal) {{
        btnShortcuts.addEventListener('click', () => shortcutsModal.classList.add('open'));
        btnCloseShortcuts.addEventListener('click', () => shortcutsModal.classList.remove('open'));
        shortcutsModal.addEventListener('click', (e) => {{
          if (e.target === shortcutsModal) shortcutsModal.classList.remove('open');
        }});
      }}

      // ── Clear Queue Modal ──────────────────────────────────────────────────
      if (btnClear && clearModal) {{
        btnCancelClear.addEventListener('click', () => clearModal.classList.remove('open'));
        btnConfirmClear.addEventListener('click', async () => {{
          clearModal.classList.remove('open');
          if (!currentGuildId) return;
          if (!sendWS('clear_queue')) {{
            await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/clear`, {{ method: 'POST' }});
          }}
          showToast('Queue cleared', 'info');
        }});
        clearModal.addEventListener('click', (e) => {{
          if (e.target === clearModal) clearModal.classList.remove('open');
        }});
      }}

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

        // Sync Live Lyrics Highlight
        if (currentLyrics && currentLyrics.length && lyricsDrawer.classList.contains('open')) {{
          let activeIdx = -1;
          for (let i = 0; i < currentLyrics.length; i++) {{
            if (elapsedSeconds >= currentLyrics[i].start && (!currentLyrics[i].end || elapsedSeconds <= currentLyrics[i].end)) {{
              activeIdx = i;
            }}
          }}
          lyricsContent.querySelectorAll('.lyric-line').forEach((el, i) => {{
            const isActive = i === activeIdx;
            el.classList.toggle('active', isActive);
            if (isActive && el !== lastScrolledLyric) {{
              lastScrolledLyric = el;
              el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
            }}
          }});
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

        // Loop state
        if (state.loop_mode !== undefined) {{
          loopMode = state.loop_mode;
          syncLoopButton();
        }}

        // Audio Effects Pills
        if (state.effects) {{
          document.querySelectorAll('.pill-btn').forEach(btn => {{
            btn.classList.toggle('active', state.effects.includes(btn.dataset.effect));
          }});
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
            playerCard && playerCard.classList.add('playing');
            if (trackArt.style.display !== 'none') {{
              trackArt.classList.add('spinning');
              trackArt.classList.remove('spin-paused');
            }}
            startElapsedTimer();
          }} else {{
            iconPlay.style.display  = 'block';
            iconPause.style.display = 'none';
            playerCard && playerCard.classList.remove('playing');
            trackArt.classList.add('spin-paused');
            stopElapsedTimer();
          }}
        }} else {{
          trackTitle.textContent = 'Playback Idle';
          trackAuthor.textContent = '—';
          trackRequester.textContent = 'No track currently active';
          trackArt.style.display = 'none';
          artPlaceholder.style.display = 'flex';
          trackArt.classList.remove('spinning', 'spin-paused');
          playerCard && playerCard.classList.remove('playing');
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

      function syncLoopButton() {{
        if (!btnLoop) return;
        btnLoop.classList.toggle('active', loopMode !== 'off');
        btnLoop.title = loopMode === 'track' ? 'Loop: Track' : loopMode === 'queue' ? 'Loop: Queue' : 'Loop: Off';
      }}

      // ── Queue Drag, Filter, Duration & Actions ────────────────────────────
      if (queueFilterInput) {{
        queueFilterInput.addEventListener('input', () => {{
          renderQueue(rawQueue);
        }});
      }}

      function renderQueue(queue) {{
        rawQueue = queue || [];
        const filterVal = (queueFilterInput ? queueFilterInput.value : '').toLowerCase().trim();
        const displayList = filterVal
          ? rawQueue.filter(item => (item.title || '').toLowerCase().includes(filterVal) || (item.uploader || '').toLowerCase().includes(filterVal))
          : rawQueue;

        let totalSeconds = rawQueue.reduce((acc, it) => acc + (it.duration || 0), 0);
        if (currentState && currentState.now_playing && currentState.now_playing.duration) {{
          totalSeconds += Math.max(0, currentState.now_playing.duration - elapsedSeconds);
        }}
        queueCount.textContent = `${{rawQueue.length}} track${{rawQueue.length !== 1 ? 's' : ''}}`;
        if (queueTotalDur) {{
          queueTotalDur.textContent = `Total: ${{formatDuration(totalSeconds)}}`;
        }}

        if (displayList.length === 0) {{
          queueList.innerHTML = '';
          queueList.appendChild(queueEmptyState);
          return;
        }}

        queueEmptyState.remove();
        queueList.innerHTML = '';

        displayList.forEach((item, displayIdx) => {{
          const origIdx = rawQueue.indexOf(item);
          const li = document.createElement('li');
          li.className = 'queue-item';
          li.draggable = true;
          li.dataset.index = origIdx;

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
            <div class="item-index">${{origIdx + 1}}</div>
            <img class="item-thumb" src="${{item.thumbnail || ''}}" onerror="this.style.display='none'" alt="" />
            <div class="item-info">
              <div class="item-title" title="${{item.title}}">${{item.title}}</div>
              <div class="item-sub">
                <span>${{item.uploader || 'Unknown'}}</span>
                ${{item.requested_by ? `<span>• by ${{item.requested_by}}</span>` : ''}}
              </div>
            </div>
            <div class="item-dur">${{formatDuration(item.duration)}}</div>
            <button class="item-playnext" title="Play next" data-index="${{origIdx}}">⏭️</button>
            <button class="item-remove" title="Remove from queue" data-index="${{origIdx}}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          `;

          // HTML5 Drag Events
          li.addEventListener('dragstart', (e) => {{
            draggedItemIndex = origIdx;
            li.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', String(origIdx));
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
            let targetIdx = origIdx;
            if (relY >= rect.height / 2) {{
              targetIdx = origIdx;
            }}
            clearDropIndicators();

            if (draggedItemIndex !== null && draggedItemIndex !== targetIdx) {{
              handleQueueMove(draggedItemIndex, targetIdx);
            }}
          }});

          // Play Next Button
          const playNextBtn = li.querySelector('.item-playnext');
          if (playNextBtn) {{
            playNextBtn.addEventListener('click', (e) => {{
              e.stopPropagation();
              handleQueueMove(origIdx, 0);
            }});
          }}

          // Remove Button
          const removeBtn = li.querySelector('.item-remove');
          removeBtn.addEventListener('click', (e) => {{
            e.stopPropagation();
            handleQueueRemove(origIdx);
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

      function adjustVolume(delta) {{
        let cur = parseInt(volumeSlider.value, 10);
        cur = Math.max(0, Math.min(200, cur + delta));
        volumeSlider.value = cur;
        volumeText.textContent = `${{cur}}%`;
        handleVolumeChange({{ target: {{ value: cur }} }});
      }}

      function toggleMute() {{
        if (isMuted) {{
          volumeSlider.value = prevVolume;
          volumeText.textContent = `${{prevVolume}}%`;
          handleVolumeChange({{ target: {{ value: prevVolume }} }});
          isMuted = false;
          showToast(`Unmuted (${{prevVolume}}%)`, 'info');
        }} else {{
          prevVolume = parseInt(volumeSlider.value, 10) || 100;
          volumeSlider.value = 0;
          volumeText.textContent = '0%';
          handleVolumeChange({{ target: {{ value: 0 }} }});
          isMuted = true;
          showToast('Muted', 'info');
        }}
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

      function handleClear() {{
        if (!currentGuildId) return;
        clearModal.classList.add('open');
      }}

      async function handleRewind() {{
        if (!currentGuildId) return;
        const targetSec = Math.max(0, elapsedSeconds - 15);
        elapsedSeconds = targetSec;
        updateScrubber();
        if (!sendWS('seek', {{ position: targetSec }})) {{
          apiFetch(`/api/v1/guild/${{currentGuildId}}/seek`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ position: targetSec }})
          }});
        }}
        showToast(`Rewound to ${{formatDuration(targetSec)}}`, 'info');
      }}

      async function handleFastForward() {{
        if (!currentGuildId) return;
        const targetSec = Math.min(totalDuration, elapsedSeconds + 15);
        elapsedSeconds = targetSec;
        updateScrubber();
        if (!sendWS('seek', {{ position: targetSec }})) {{
          apiFetch(`/api/v1/guild/${{currentGuildId}}/seek`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ position: targetSec }})
          }});
        }}
        showToast(`Forwarded to ${{formatDuration(targetSec)}}`, 'info');
      }}

      async function handleLoopToggle() {{
        if (!currentGuildId) return;
        const next = loopMode === 'off' ? 'track' : loopMode === 'track' ? 'queue' : 'off';
        if (!sendWS('loop', {{ mode: next }})) {{
          apiFetch(`/api/v1/guild/${{currentGuildId}}/loop`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ mode: next }})
          }});
        }}
        loopMode = next;
        syncLoopButton();
        const label = next === 'off' ? 'Loop off' : next === 'track' ? 'Loop: Track' : 'Loop: Queue';
        showToast(label, 'info');
      }}

      // ── Live Lyrics ────────────────────────────────────────────────────────
      async function loadLyrics() {{
        if (!currentGuildId) return;
        lyricsTrackTitle.textContent = trackTitle.textContent;
        lyricsContent.innerHTML = '<div style="text-align:center; color:var(--text-dim); margin-top:3rem;">Loading synced lyrics…</div>';
        try {{
          const res = await apiFetch(`/api/v1/guild/${{currentGuildId}}/lyrics`);
          const data = await res.json();
          if (data && data.success && data.lines && data.lines.length) {{
            currentLyrics = data.lines;
            lyricsContent.innerHTML = data.lines.map((l, i) =>
              `<div class="lyric-line" data-start="${{l.start}}" data-end="${{l.end}}" data-index="${{i}}">${{l.text}}</div>`
            ).join('');
            lyricsContent.querySelectorAll('.lyric-line').forEach(el => {{
              el.addEventListener('click', () => {{
                const targetSec = parseFloat(el.dataset.start);
                elapsedSeconds = targetSec;
                updateScrubber();
                sendWS('seek', {{ position: Math.floor(targetSec) }});
              }});
            }});
          }} else {{
            currentLyrics = null;
            lyricsContent.innerHTML = `<div style="text-align:center; color:var(--text-dim); margin-top:3rem;">${{(data && data.error) || 'No lyrics available for this track.'}}</div>`;
          }}
        }} catch (err) {{
          lyricsContent.innerHTML = '<div style="text-align:center; color:var(--red); margin-top:3rem;">Failed to fetch lyrics.</div>';
        }}
      }}

      if (btnLyricsToggle) {{
        btnLyricsToggle.addEventListener('click', () => {{
          lyricsDrawer.classList.toggle('open');
          if (lyricsDrawer.classList.contains('open')) {{
            loadLyrics();
          }}
        }});
      }}
      if (btnLyricsClose) {{
        btnLyricsClose.addEventListener('click', () => lyricsDrawer.classList.remove('open'));
      }}

      // ── Instant Search & Add ───────────────────────────────────────────────
      async function executeSearch() {{
        const query = (searchInput.value || '').trim();
        if (!query) return;
        btnSearchGo.textContent = '…';
        try {{
          const res = await apiFetch(`/api/v1/dashboard/search?q=${{encodeURIComponent(query)}}&limit=5`);
          const data = await res.json();
          btnSearchGo.textContent = 'Search';
          if (data && data.results && data.results.length) {{
            searchDropdown.innerHTML = data.results.map(item => `
              <div class="search-result-item">
                <img class="search-result-thumb" src="${{item.thumbnail || ''}}" onerror="this.style.display='none'" />
                <div class="search-result-info">
                  <div class="search-result-title" title="${{item.title}}">${{item.title}}</div>
                  <div class="search-result-sub">${{item.uploader || 'Unknown'}} • ${{formatDuration(item.duration)}}</div>
                </div>
                <div class="search-result-btns">
                  <button class="btn-mini-add" data-url="${{item.url}}" data-action="queue">➕ Queue</button>
                  <button class="btn-mini-add" data-url="${{item.url}}" data-action="next">⏭️ Next</button>
                </div>
              </div>
            `).join('');
            searchDropdown.style.display = 'flex';

            searchDropdown.querySelectorAll('.btn-mini-add').forEach(btn => {{
              btn.addEventListener('click', async (e) => {{
                e.stopPropagation();
                const url = btn.dataset.url;
                const isNext = btn.dataset.action === 'next';
                if (!currentGuildId) return;
                if (!sendWS(isNext ? 'play_next' : 'add_to_queue', {{ query: url, play_next: isNext }})) {{
                  await apiFetch(`/api/v1/guild/${{currentGuildId}}/queue/add`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ query: url, play_next: isNext }})
                  }});
                }}
                showToast(isNext ? 'Playing next…' : 'Added to queue', 'success');
                searchDropdown.style.display = 'none';
                searchInput.value = '';
              }});
            }});
          }} else {{
            searchDropdown.innerHTML = '<div style="padding:0.75rem; text-align:center; color:var(--text-dim); font-size:0.8rem">No results found</div>';
            searchDropdown.style.display = 'flex';
          }}
        }} catch (err) {{
          btnSearchGo.textContent = 'Search';
          showToast('Search failed', 'error');
        }}
      }}

      if (btnSearchGo) {{
        btnSearchGo.addEventListener('click', (e) => {{
          e.stopPropagation();
          executeSearch();
        }});
      }}
      if (searchInput) {{
        searchInput.addEventListener('keydown', (e) => {{
          if (e.key === 'Enter') {{
            executeSearch();
          }}
        }});
        searchInput.addEventListener('click', (e) => e.stopPropagation());
      }}

      // ── Audio Effects Pills ────────────────────────────────────────────────
      document.querySelectorAll('.pill-btn').forEach(btn => {{
        btn.addEventListener('click', async () => {{
          if (!currentGuildId) return;
          const effect = btn.dataset.effect;
          if (!sendWS('toggle_effect', {{ effect }})) {{
            await apiFetch(`/api/v1/guild/${{currentGuildId}}/effects`, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ effect }})
            }});
          }}
          btn.classList.toggle('active');
          showToast(`Toggled effect: ${{effect}}`, 'info');
        }});
      }});

      // ── Keyboard Shortcuts ─────────────────────────────────────────────────
      window.addEventListener('keydown', (e) => {{
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return;

        if (e.code === 'Space') {{
          e.preventDefault();
          handlePlayPause();
        }} else if (e.key === 'ArrowLeft') {{
          e.preventDefault();
          handleRewind();
        }} else if (e.key === 'ArrowRight') {{
          e.preventDefault();
          handleFastForward();
        }} else if (e.key === 'ArrowUp') {{
          e.preventDefault();
          adjustVolume(5);
        }} else if (e.key === 'ArrowDown') {{
          e.preventDefault();
          adjustVolume(-5);
        }} else if (e.key === 'n' || e.key === 'N') {{
          handleSkip();
        }} else if (e.key === 'l' || e.key === 'L') {{
          handleLoopToggle();
        }} else if (e.key === 'm' || e.key === 'M') {{
          toggleMute();
        }} else if (e.key === '?') {{
          if (shortcutsModal) shortcutsModal.classList.toggle('open');
        }}
      }});

      // ── Event Listeners ────────────────────────────────────────────────────
      guildSelect.addEventListener('change', (e) => {{
        currentGuildId = e.target.value;
        sendWS('select_guild', {{ guild_id: currentGuildId }});
      }});

      btnPlayPause.addEventListener('click', handlePlayPause);
      btnSkip.addEventListener('click', handleSkip);
      if (btnRewind) btnRewind.addEventListener('click', handleRewind);
      if (btnLoop) btnLoop.addEventListener('click', handleLoopToggle);
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
