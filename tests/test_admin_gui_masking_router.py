"""Tests for `/api/masking/*`: pattern CRUD and the match-sample audit view,
driven against the real FastAPI app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from mcp_servers.shared.masking import mask_text


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_patterns_includes_the_three_built_ins(client: TestClient) -> None:
    response = client.get("/api/masking/patterns")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert {"EMAIL", "IPV4", "PHONE"} <= names


def test_create_pattern_then_it_is_listed(client: TestClient) -> None:
    response = client.post(
        "/api/masking/patterns", json={"name": "SERVER_ID", "regex": "srv-[0-9]+"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["built_in"] is False
    assert body["enabled"] is True

    names = {p["name"] for p in client.get("/api/masking/patterns").json()}
    assert "SERVER_ID" in names


def test_create_pattern_rejects_invalid_regex(client: TestClient) -> None:
    response = client.post("/api/masking/patterns", json={"name": "BROKEN", "regex": "[unclosed"})
    assert response.status_code == 400


def test_create_pattern_rejects_duplicate_name(client: TestClient) -> None:
    client.post("/api/masking/patterns", json={"name": "SERVER_ID", "regex": "srv-[0-9]+"})
    response = client.post(
        "/api/masking/patterns", json={"name": "SERVER_ID", "regex": "task-[0-9]+"}
    )
    assert response.status_code == 409


def test_update_pattern_can_disable_it(client: TestClient) -> None:
    created = client.post(
        "/api/masking/patterns", json={"name": "SERVER_ID", "regex": "srv-[0-9]+"}
    ).json()
    response = client.put(f"/api/masking/patterns/{created['id']}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_update_unknown_pattern_is_404(client: TestClient) -> None:
    response = client.put("/api/masking/patterns/999999", json={"enabled": False})
    assert response.status_code == 404


def test_delete_custom_pattern_succeeds(client: TestClient) -> None:
    created = client.post(
        "/api/masking/patterns", json={"name": "SERVER_ID", "regex": "srv-[0-9]+"}
    ).json()
    response = client.delete(f"/api/masking/patterns/{created['id']}")
    assert response.status_code == 204


def test_delete_built_in_pattern_is_rejected(client: TestClient) -> None:
    patterns = client.get("/api/masking/patterns").json()
    email = next(p for p in patterns if p["name"] == "EMAIL")
    response = client.delete(f"/api/masking/patterns/{email['id']}")
    assert response.status_code == 400


def test_samples_are_listed_and_can_be_cleared(client: TestClient) -> None:
    patterns = client.get("/api/masking/patterns").json()
    email = next(p for p in patterns if p["name"] == "EMAIL")

    mask_text("contact ops@example.com", source="test")

    samples = client.get(f"/api/masking/patterns/{email['id']}/samples").json()
    assert len(samples) == 1
    assert samples[0]["matched_text"] == "ops@example.com"

    clear_response = client.delete(f"/api/masking/patterns/{email['id']}/samples")
    assert clear_response.status_code == 204
    assert client.get(f"/api/masking/patterns/{email['id']}/samples").json() == []
