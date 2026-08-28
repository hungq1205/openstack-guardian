"""Tests for `admin_gui.backend.app`'s prod static-serving mode: the built
frontend mounted behind the same FastAPI process, with SPA fallback for
client-side routes -- and that this never shadows the `/api/*` routers.

Uses an isolated `tmp_path` "fake build" (`create_app(frontend_dist=...)`)
rather than the real `admin_gui/frontend/dist`, so these tests pass whether
or not `npm run build` has actually been run in this checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import create_app


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>fake spa shell</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('fake bundle');", encoding="utf-8")
    return dist


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CMP_ADMIN_V2_BASE_URL", "CMP_LOGS_ES_URL", "CMP_NOTIFY_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


def test_without_a_build_only_the_api_is_served() -> None:
    app = create_app(frontend_dist=Path("does-not-exist"))
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 404


def test_root_serves_the_built_index_html(fake_dist: Path) -> None:
    client = TestClient(create_app(frontend_dist=fake_dist))
    response = client.get("/")
    assert response.status_code == 200
    assert "fake spa shell" in response.text


def test_a_client_side_route_falls_back_to_index_html(fake_dist: Path) -> None:
    """A full page load of e.g. /catalog (not just an in-app navigation)
    must still get the SPA shell so React Router can take over client-side."""
    client = TestClient(create_app(frontend_dist=fake_dist))
    response = client.get("/catalog")
    assert response.status_code == 200
    assert "fake spa shell" in response.text


def test_assets_are_served_from_the_assets_mount(fake_dist: Path) -> None:
    client = TestClient(create_app(frontend_dist=fake_dist))
    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert "fake bundle" in response.text


def test_api_routes_are_not_shadowed_by_the_spa_fallback(fake_dist: Path) -> None:
    client = TestClient(create_app(frontend_dist=fake_dist))
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/servers").status_code == 200
