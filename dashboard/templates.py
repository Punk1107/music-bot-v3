# -*- coding: utf-8 -*-
"""
dashboard/templates.py — Template loader and bundle renderer for the Next-Gen Web Dashboard 2.0.

Loads dashboard/templates/index.html and inlines dashboard/static/ assets for 100% self-contained
offline execution while allowing clean, modular file separation.
"""

from __future__ import annotations

from pathlib import Path


def render_dashboard_html(port: int = 8080, api_secret: str = "") -> str:
    """
    Render dashboard HTML.
    Loads templates/index.html and inlines static CSS and JS for 100% self-contained offline delivery.
    """
    base_dir = Path(__file__).parent
    index_path = base_dir / "templates" / "index.html"
    css_path = base_dir / "static" / "css" / "dashboard.css"
    vis_path = base_dir / "static" / "js" / "visualizer.js"
    eq_path = base_dir / "static" / "js" / "equalizer.js"
    app_path = base_dir / "static" / "js" / "app.js"

    html = index_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    vis_js = vis_path.read_text(encoding="utf-8") if vis_path.exists() else ""
    eq_js = eq_path.read_text(encoding="utf-8") if eq_path.exists() else ""
    app_js = app_path.read_text(encoding="utf-8") if app_path.exists() else ""

    # Replace configuration placeholders
    html = html.replace("{{PORT}}", str(port)).replace("{{API_SECRET}}", api_secret)

    # Inline CSS & JS for complete self-contained offline compliance
    if css:
        html = html.replace(
            '<link rel="stylesheet" href="/static/css/dashboard.css">',
            f"<style>\n{css}\n</style>",
        )
    if vis_js or eq_js or app_js:
        bundle_js = f"\n<script>\n{vis_js}\n{eq_js}\n{app_js}\n</script>\n"
        html = html.replace('<script src="/static/js/visualizer.js"></script>', "")
        html = html.replace('<script src="/static/js/equalizer.js"></script>', "")
        html = html.replace('<script src="/static/js/app.js"></script>', bundle_js)

    return html
