from __future__ import annotations

from pathlib import Path

from openparkcad import cli
from openparkcad.review_bundle import build_review_bundle
from openparkcad.road_traversal import apply_road_traversal
from openparkcad.viewer import write_review_html
from tests.road_traversal_support import through_layout


def test_viewer_escapes_bundle_text_and_does_not_execute_it(tmp_path: Path) -> None:
    layout = apply_road_traversal(through_layout())
    bundle = build_review_bundle(layout)
    bundle["site"]["name"] = "lot <script>alert(1)</script> & \"q\""
    html_path = tmp_path / "review.html"
    write_review_html(bundle, html_path)
    raw = html_path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in raw
    assert "\\u003cscript\\u003e" in raw or "&lt;script&gt;" in raw
    assert (tmp_path / "review.js").is_file()
    assert (tmp_path / "review.css").is_file()
    assert 'type="application/json"' in raw
    assert "<script src=" in raw


def test_viewer_escapes_object_ids_that_would_xss_via_innerhtml(tmp_path: Path) -> None:
    layout = apply_road_traversal(through_layout())
    bundle = build_review_bundle(layout)
    evil = 'x"><script>alert(1)</script>'
    for candidate in bundle["candidates"]:
        geometry = candidate.get("geometry") or {}
        for stall in geometry.get("stalls") or []:
            stall["id"] = evil
        for aisle in geometry.get("aisles") or []:
            aisle["id"] = evil
    bundle["failures"] = [{"object_id": evil, "reason": "<img src=x onerror=alert(1)>"}]
    html_path = tmp_path / "review.html"
    write_review_html(bundle, html_path)
    js = (tmp_path / "review.js").read_text(encoding="utf-8")
    assert "function escapeAttr" in js
    assert "function escapeText" in js
    assert "escapeAttr(a.id" in js
    assert "escapeAttr(s.id" in js
    assert "escapeAttr(j.stall_id" in js
    assert "escapeAttr(f.object_id" in js
    assert "escapeText(f.object_id" in js
    assert 'data-object-id="' + "' + (s.id" not in js
    raw = html_path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in raw


def test_view_cli_writes_html(tmp_path: Path) -> None:
    layout = apply_road_traversal(through_layout())
    bundle_path = tmp_path / "bundle.json"
    html_path = tmp_path / "out.html"
    bundle_path.write_text(__import__("json").dumps(build_review_bundle(layout)), encoding="utf-8")
    assert cli.main(["view", str(bundle_path), "--out", str(html_path)]) == 0
    assert html_path.is_file()
    raw = html_path.read_text(encoding="utf-8")
    assert 'id="scene"' in raw
    assert "review.js" in raw
    assert "review-bundle-1" in raw
