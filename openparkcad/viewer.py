"""Offline HTML/SVG/JS viewer for review-bundle-1. Does not execute bundle strings."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).resolve().parent / "viewer_assets"


def write_review_html(bundle: dict[str, Any], path: str | Path) -> None:
    if bundle.get("version") != "review-bundle-1":
        raise ValueError("review bundle version must be review-bundle-1")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    title = escape(str((bundle.get("site") or {}).get("name") or "OpenParkCAD review"))
    js_name = "review.js"
    css_name = "review.css"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" href="{escape(css_name)}"/>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p id="status-line"></p>
    <p id="skeleton-line"></p>
  </header>
  <div id="toolbar">
    <label>Candidate <select id="candidate-select"></select></label>
    <label><input type="checkbox" id="layer-aisles" checked/> aisles</label>
    <label><input type="checkbox" id="layer-stalls" checked/> stalls</label>
    <label><input type="checkbox" id="layer-journeys" checked/> journeys</label>
    <label><input type="checkbox" id="layer-failures" checked/> failures</label>
    <button type="button" id="play-journey">Play stall journey</button>
  </div>
  <div id="layout">
    <svg id="scene" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>
    <aside id="details">
      <h2>Details</h2>
      <pre id="detail-text"></pre>
    </aside>
  </div>
  <script type="application/json" id="review-bundle">{encoded}</script>
  <script src="{escape(js_name)}"></script>
</body>
</html>
"""
    target.write_text(html, encoding="utf-8")
    js_source = ASSETS_DIR / "review.js"
    css_source = ASSETS_DIR / "review.css"
    if not js_source.is_file() or not css_source.is_file():
        raise FileNotFoundError("viewer assets are missing from the installed package")
    (target.parent / js_name).write_text(js_source.read_text(encoding="utf-8"), encoding="utf-8")
    (target.parent / css_name).write_text(css_source.read_text(encoding="utf-8"), encoding="utf-8")
