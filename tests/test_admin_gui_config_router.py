"""Tests for `/api/config/*`: connection config CRUD with secret masking on
read, and the three test-connection endpoints -- all driven against fakes
(`httpx.MockTransport`) so no real network call is ever made, matching this
project's existing HTTP-faking convention.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from mcp_servers.cmp_logs_mcp import client as es_client_module
from mcp_servers.cmp_notify_mcp import client as notify_client_module
from mcp_servers.openapi_bridge import CmpApiClient


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CMP_ADMIN_V2_BASE_URL",
        "CMP_ADMIN_V2_PAT",
        "CMP_LOGS_ES_URL",
        "CMP_NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_connection_is_empty_by_default(client: TestClient) -> None:
    response = client.get("/api/config/cmp-admin-v2")
    assert response.status_code == 200
    assert response.json() == {}


def test_unknown_connection_is_404(client: TestClient) -> None:
    response = client.get("/api/config/bogus")
    assert response.status_code == 404


def test_put_then_get_masks_secret_fields(client: TestClient) -> None:
    response = client.put(
        "/api/config/cmp-admin-v2",
        json={"base_url": "https://cmp.example.com", "pat": "sk-abcdef1234"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "https://cmp.example.com"
    assert body["pat"] != "sk-abcdef1234"
    assert body["pat"].endswith("1234")
    assert "sk-abcdef" not in body["pat"]

    get_response = client.get("/api/config/cmp-admin-v2")
    assert get_response.json()["pat"] == body["pat"]


def test_put_merges_rather_than_overwrites(client: TestClient) -> None:
    client.put(
        "/api/config/cmp-admin-v2", json={"base_url": "https://cmp.example.com", "pat": "secret"}
    )
    response = client.put(
        "/api/config/cmp-admin-v2", json={"base_url": "https://updated.example.com"}
    )
    assert response.json()["base_url"] == "https://updated.example.com"
    assert response.json()["pat"].endswith("cret")  # untouched, still present


def test_cmp_admin_test_connection_reports_not_reachable_without_base_url(
    client: TestClient,
) -> None:
    response = client.post("/api/config/cmp-admin-v2/test-connection", json={})
    assert response.status_code == 200
    assert response.json()["reachable"] is False


def test_cmp_admin_test_connection_uses_a_real_client_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "token my-pat"
        return httpx.Response(200, json={"servers": []})

    real_call = CmpApiClient.call

    def fake_call(self: CmpApiClient, op, args, *, client=None):  # type: ignore[no-untyped-def]
        return real_call(
            self, op, args, client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(CmpApiClient, "call", fake_call)

    response = client.post(
        "/api/config/cmp-admin-v2/test-connection",
        json={"base_url": "https://cmp.example.com", "pat": "my-pat"},
    )
    assert response.status_code == 200
    assert response.json() == {"reachable": True, "status_code": 200}


def test_cmp_admin_test_connection_falls_back_when_list_compute_nodes_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: disabling the built-in "server" domain (e.g. switching to
    server_v1 only, from the Spec Sources page) used to crash this endpoint
    with an unhandled KeyError -- list_compute_nodes was hardcoded as if it
    always exists. It must fall back to any other read-only, parameterless
    GET instead of raising."""
    from mcp_servers.shared import config_store

    config_store.add_spec_source("cmp-admin", "server", "unused", domain="server")
    config_store.set_spec_source_enabled(
        next(
            row.id
            for row in config_store.get_spec_sources("cmp-admin", enabled_only=False)
            if row.domain == "server"
        ),
        False,
    )

    captured: dict[str, object] = {}
    real_call = CmpApiClient.call

    def fake_call(self: CmpApiClient, op, args, *, client=None):  # type: ignore[no-untyped-def]
        captured["operation_id"] = op.operation_id
        return real_call(
            self,
            op,
            args,
            client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
            ),
        )

    monkeypatch.setattr(CmpApiClient, "call", fake_call)

    response = client.post(
        "/api/config/cmp-admin-v2/test-connection",
        json={"base_url": "https://cmp.example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"reachable": True, "status_code": 200}
    assert captured["operation_id"] != "list_compute_nodes"


def test_elasticsearch_test_connection_reports_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 3})

    original = es_client_module.ElasticsearchLogsClient.check_connection

    def fake_check_connection(self, *, client=None):  # type: ignore[no-untyped-def]
        return original(self, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(
        es_client_module.ElasticsearchLogsClient, "check_connection", fake_check_connection
    )

    response = client.post(
        "/api/config/elasticsearch/test-connection",
        json={"url": "https://es.example.com", "index": "logs"},
    )
    assert response.json() == {"reachable": True, "status_code": 200}


def test_notify_test_connection_sends_a_distinct_test_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200)

    original = notify_client_module.NotifyClient.send_test

    def fake_send_test(self, *, client=None):  # type: ignore[no-untyped-def]
        return original(self, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(notify_client_module.NotifyClient, "send_test", fake_send_test)

    response = client.post(
        "/api/config/notify/test-connection", json={"webhook_url": "https://hooks.example.com/x"}
    )
    assert response.json() == {"reachable": True, "status_code": 200}
    assert b'"test":true' in captured["body"] or b'"test": true' in captured["body"]
