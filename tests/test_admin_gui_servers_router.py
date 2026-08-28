"""Tests for the admin GUI's `/api/servers/*` introspection routes -- driven
against the real FastAPI app via `TestClient`, exercising the actual
`build_admin_server`/`build_logs_server`/`build_notify_server` builder
functions in-process (no mocks), matching this project's existing rigor for
protocol-level tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from mcp_servers.shared import config_store


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CMP_ADMIN_V2_BASE_URL",
        "CMP_ADMIN_V2_PAT",
        "CMP_ADMIN_V2_USERNAME",
        "CMP_ADMIN_V2_PASSWORD",
        "CMP_LOGS_ES_URL",
        "CMP_LOGS_ES_INDEX",
        "CMP_NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_check(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_servers_returns_all_three(client: TestClient) -> None:
    response = client.get("/api/servers")
    assert response.status_code == 200
    ids = {entry["id"] for entry in response.json()}
    assert ids == {"cmp-admin", "cmp-logs", "cmp-notify"}


def test_list_tools_for_cmp_admin_returns_the_pinned_discovery_subset(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/tools")
    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert "rebuild_server" in names
    assert "search_tools" in names
    assert "search_failure_patterns" in names
    # Progressive discovery: nowhere near the full ~104-operation catalog.
    assert len(names) < 20


def test_list_tools_for_cmp_logs_returns_both_tools(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-logs/tools")
    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == [
        "search_logs",
        "follow_request_id",
    ]


def test_list_tools_for_unknown_server_is_404(client: TestClient) -> None:
    response = client.get("/api/servers/bogus/tools")
    assert response.status_code == 404


def test_list_all_admin_operations_returns_the_full_catalog(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/tools/all")
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) > 100
    by_id = {entry["operation_id"]: entry for entry in catalog}
    assert by_id["rebuild_server"]["pinned"] is True
    assert by_id["list_volume_qos"]["pinned"] is False


def test_pin_toggle_updates_the_full_catalog(client: TestClient) -> None:
    response = client.post(
        "/api/servers/cmp-admin/tools/list_volume_qos/pin", json={"pinned": True}
    )
    assert response.status_code == 200
    assert response.json() == {"pinned": True}

    catalog = client.get("/api/servers/cmp-admin/tools/all").json()
    by_id = {entry["operation_id"]: entry for entry in catalog}
    assert by_id["list_volume_qos"]["pinned"] is True
    assert config_store.get_pinned_overrides()["list_volume_qos"] is True


def test_colliding_spec_sources_produce_a_clean_400_not_an_unhandled_500(client: TestClient) -> None:
    """Reproduces a real GUI bug: adding a second spec source for cmp-admin
    whose operations collide with an already-configured one (trivial to do
    by accident -- e.g. re-uploading the same file) used to bubble the raw
    `ValueError` from `merge_operations` straight into an unhandled 500."""
    with open("mcp_servers/specs/network.json", "rb") as f:
        network_spec = f.read()

    first = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "network copy 1"},
        files={"spec_file": ("network.json", network_spec, "application/json")},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "network copy 2"},
        files={"spec_file": ("network-again.json", network_spec, "application/json")},
    )
    assert second.status_code == 201

    response = client.get("/api/servers/cmp-admin/tools/all")
    assert response.status_code == 400
    assert "operation_id collision" in response.json()["detail"]

    response = client.get("/api/servers/cmp-admin/tools")
    assert response.status_code == 400
    assert "operation_id collision" in response.json()["detail"]


def test_get_operation_detail_returns_full_spec_and_annotation(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/tools/rebuild_server")
    assert response.status_code == 200
    detail = response.json()
    assert detail["operation_id"] == "rebuild_server"
    assert detail["method"] == "POST"
    assert detail["category"] == "action"
    assert detail["risk_level"] == "medium"
    assert detail["related_tools"] == []
    assert detail["pinned"] is True
    assert any(p["name"] == "server_id" for p in detail["parameters"])


def test_get_operation_detail_for_unknown_operation_is_404(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/tools/not_a_real_operation")
    assert response.status_code == 404


def test_update_operation_detail_persists_and_is_reflected_in_the_catalog(client: TestClient) -> None:
    response = client.put(
        "/api/servers/cmp-admin/tools/list_volume_qos",
        json={"risk_level": "high", "usage_note": "edited via the GUI"},
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["risk_level"] == "high"
    assert detail["usage_note"] == "edited via the GUI"

    catalog = client.get("/api/servers/cmp-admin/tools/all").json()
    by_id = {entry["operation_id"]: entry for entry in catalog}
    assert by_id["list_volume_qos"]["risk_level"] == "high"

    reread = client.get("/api/servers/cmp-admin/tools/list_volume_qos").json()
    assert reread["risk_level"] == "high"
    assert reread["usage_note"] == "edited via the GUI"


def test_update_operation_detail_merges_rather_than_clobbers_prior_edits(client: TestClient) -> None:
    client.put("/api/servers/cmp-admin/tools/list_volume_qos", json={"risk_level": "high"})
    response = client.put(
        "/api/servers/cmp-admin/tools/list_volume_qos", json={"usage_note": "second edit"}
    )
    detail = response.json()
    assert detail["risk_level"] == "high"
    assert detail["usage_note"] == "second edit"


def test_list_resource_templates_for_cmp_admin_returns_six(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/resources")
    assert response.status_code == 200
    assert len(response.json()) == 6


def test_list_resource_templates_for_cmp_logs_is_empty_not_an_error(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-logs/resources")
    assert response.status_code == 200
    assert response.json() == []


def test_list_prompts_for_cmp_admin_returns_assemble_log(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-admin/prompts")
    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == ["assemble_log"]


def test_list_prompts_for_cmp_notify_is_empty_not_an_error(client: TestClient) -> None:
    response = client.get("/api/servers/cmp-notify/prompts")
    assert response.status_code == 200
    assert response.json() == []
