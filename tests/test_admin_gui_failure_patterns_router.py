"""Tests for `/api/failure-patterns`: CRUD against the knowledge-base JSON
file, driven through the real FastAPI app.

Every test redirects `failure_pattern_matcher._DEFAULT_PATH` to a `tmp_path`
copy -- the router has no way to pass a path through an HTTP request, so
without this redirect these tests would mutate the real project file.
`load_failure_patterns`/`add_failure_pattern`/etc. resolve `_DEFAULT_PATH` at
call time specifically so this monkeypatch works (see that module's
`load_failure_patterns` docstring).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from mcp_servers.cmp_admin_mcp import failure_pattern_matcher as fpm

_REAL_PATH = (
    Path(__file__).resolve().parent.parent
    / "mcp_servers"
    / "cmp_admin_mcp"
    / "knowledge"
    / "failure_patterns.json"
)

_VALID_NEW_PATTERN = {
    "id": "test_pattern",
    "signature_pattern": "something failed for {resource_id}",
    "cause": "a made-up cause for testing",
    "instruction": "test instruction for handling this pattern",
    "source_reference": "test suite",
}


@pytest.fixture(autouse=True)
def _use_tmp_kb(tmp_path, monkeypatch: pytest.MonkeyPatch):
    destination = tmp_path / "failure_patterns.json"
    shutil.copy(_REAL_PATH, destination)
    monkeypatch.setattr(fpm, "_DEFAULT_PATH", destination)
    fpm.reload_failure_patterns()
    yield destination
    fpm.reload_failure_patterns()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_returns_the_real_twelve_entries(client: TestClient) -> None:
    response = client.get("/api/failure-patterns")
    assert response.status_code == 200
    assert len(response.json()) == 12


def test_create_then_it_is_listed(client: TestClient) -> None:
    response = client.post("/api/failure-patterns", json=_VALID_NEW_PATTERN)
    assert response.status_code == 201

    ids = {p["id"] for p in client.get("/api/failure-patterns").json()}
    assert "test_pattern" in ids


def test_create_rejects_invalid_record(client: TestClient) -> None:
    response = client.post("/api/failure-patterns", json={"id": "missing_fields"})
    assert response.status_code == 400


def test_create_rejects_duplicate_id(client: TestClient) -> None:
    response = client.post(
        "/api/failure-patterns", json={**_VALID_NEW_PATTERN, "id": "volume_status_drift"}
    )
    assert response.status_code == 409


def test_update_replaces_the_record(client: TestClient) -> None:
    updated = {**_VALID_NEW_PATTERN, "id": "volume_status_drift", "cause": "a different cause"}
    response = client.put("/api/failure-patterns/volume_status_drift", json=updated)
    assert response.status_code == 200

    patterns = client.get("/api/failure-patterns").json()
    record = next(p for p in patterns if p["id"] == "volume_status_drift")
    assert record["cause"] == "a different cause"


def test_update_unknown_id_is_404(client: TestClient) -> None:
    response = client.put("/api/failure-patterns/does_not_exist", json=_VALID_NEW_PATTERN)
    assert response.status_code == 404


def test_delete_removes_the_record(client: TestClient) -> None:
    response = client.delete("/api/failure-patterns/volume_status_drift")
    assert response.status_code == 204

    ids = {p["id"] for p in client.get("/api/failure-patterns").json()}
    assert "volume_status_drift" not in ids


def test_delete_unknown_id_is_404(client: TestClient) -> None:
    response = client.delete("/api/failure-patterns/does_not_exist")
    assert response.status_code == 404


def test_real_project_file_is_never_touched(_use_tmp_kb: Path) -> None:
    """The autouse fixture yields the tmp copy's path -- confirms every test
    in this module really is redirected away from the real file."""
    assert _use_tmp_kb != _REAL_PATH
    assert fpm._DEFAULT_PATH == _use_tmp_kb
