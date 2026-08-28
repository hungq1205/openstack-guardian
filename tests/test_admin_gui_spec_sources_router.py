"""Tests for `/api/spec-sources`: every built-in domain (server/
block_storage/network, plus the opt-in server_v1 alternate) as an
always-listed, editable/toggleable row, plus CRUD and multipart upload for
purely additive extra pairs -- driven against the real FastAPI app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app

_VALID_SPEC = (
    b'{"openapi": "3.1.0", "info": {"title": "custom", "version": "1"}, '
    b'"paths": {"/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}}}'
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_spec_sources_always_shows_every_built_in_domain(client: TestClient) -> None:
    response = client.get("/api/spec-sources", params={"server": "cmp-admin"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 4
    domains = {row["domain"] for row in rows}
    assert domains == {"server", "block_storage", "network", "server_v1"}
    assert all(row["id"] is None for row in rows)  # untouched -- synthesized, not materialized
    assert all(row["spec_path"].endswith(".json") for row in rows)

    by_domain = {row["domain"]: row for row in rows}
    assert by_domain["server"]["enabled"] is True
    assert by_domain["block_storage"]["enabled"] is True
    assert by_domain["network"]["enabled"] is True
    assert by_domain["server_v1"]["enabled"] is False  # ships opt-in, not active by default


def test_upload_a_valid_spec_file_adds_alongside_the_built_ins(client: TestClient) -> None:
    response = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "custom pair"},
        files={"spec_file": ("custom.json", _VALID_SPEC, "application/json")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["server"] == "cmp-admin"
    assert body["name"] == "custom pair"
    assert body["domain"] is None
    assert body["annotations_path"] is None
    assert body["enabled"] is True
    # The original filename is preserved on disk, not replaced with an
    # opaque random name -- only the containing directory is randomized.
    assert body["spec_path"].endswith("custom.json")

    listed = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    assert len(listed) == 5  # 4 built-in domains + this one extra
    extra = next(row for row in listed if row["domain"] is None)
    assert extra["id"] == body["id"]
    assert extra["name"] == "custom pair"


def test_upload_requires_a_name(client: TestClient) -> None:
    response = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "   "},
        files={"spec_file": ("custom.json", _VALID_SPEC, "application/json")},
    )
    assert response.status_code == 400


def test_upload_rejects_invalid_json(client: TestClient) -> None:
    response = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "bad"},
        files={"spec_file": ("bad.json", b"not json at all", "application/json")},
    )
    assert response.status_code == 400


def test_toggle_enabled_and_delete_an_extra(client: TestClient) -> None:
    created = client.post(
        "/api/spec-sources",
        data={"server": "cmp-admin", "name": "custom pair"},
        files={"spec_file": ("custom.json", _VALID_SPEC, "application/json")},
    ).json()

    disable_response = client.put(f"/api/spec-sources/{created['id']}", json={"enabled": False})
    assert disable_response.status_code == 200
    assert disable_response.json() == {"enabled": False}

    delete_response = client.delete(f"/api/spec-sources/{created['id']}")
    assert delete_response.status_code == 204
    # Deleting the extra removes it entirely -- back to just the built-in domains.
    remaining = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    assert len(remaining) == 4
    assert all(row["domain"] is not None for row in remaining)


def test_edit_a_built_in_domain_materializes_an_override_row(client: TestClient) -> None:
    with open("mcp_servers/specs/network.json", "rb") as f:
        network_spec = f.read()

    response = client.put(
        "/api/spec-sources/built-in/network",
        data={"server": "cmp-admin", "name": "my network"},
        files={"spec_file": ("my-network.json", network_spec, "application/json")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] is not None
    assert body["domain"] == "network"
    assert body["name"] == "my network"
    assert body["spec_path"].endswith("my-network.json")

    listed = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    by_domain = {row["domain"]: row for row in listed}
    assert len(listed) == 4
    assert by_domain["network"]["id"] is not None
    assert by_domain["network"]["name"] == "my network"
    assert by_domain["server"]["id"] is None  # untouched domains stay virtual


def test_edit_a_built_in_domain_rejects_an_unknown_domain(client: TestClient) -> None:
    response = client.put(
        "/api/spec-sources/built-in/not-a-real-domain",
        data={"server": "cmp-admin", "name": "x"},
    )
    assert response.status_code == 404


def test_disabling_a_built_in_domain_removes_it_from_the_catalog(client: TestClient) -> None:
    response = client.put(
        "/api/spec-sources/built-in/network", data={"server": "cmp-admin", "enabled": "false"}
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    listed = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    by_domain = {row["domain"]: row for row in listed}
    assert by_domain["network"]["enabled"] is False

    catalog = client.get("/api/servers/cmp-admin/tools/all")
    assert catalog.status_code == 200
    by_id = {entry["operation_id"]: entry for entry in catalog.json()}
    assert "list_elastic_ips" not in by_id  # a network-only operation, now gone
    assert "rebuild_server" in by_id  # server/block_storage untouched


def test_deleting_a_materialized_built_in_row_resets_it_to_default(client: TestClient) -> None:
    client.put("/api/spec-sources/built-in/network", data={"server": "cmp-admin", "name": "renamed"})
    materialized = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    row_id = next(row for row in materialized if row["domain"] == "network")["id"]
    assert row_id is not None

    delete_response = client.delete(f"/api/spec-sources/{row_id}")
    assert delete_response.status_code == 204

    listed = client.get("/api/spec-sources", params={"server": "cmp-admin"}).json()
    by_domain = {row["domain"]: row for row in listed}
    assert by_domain["network"]["id"] is None  # back to virtual/default
    assert by_domain["network"]["name"] == "network"


def test_disabling_a_domain_resources_depend_on_degrades_gracefully(client: TestClient) -> None:
    """Disabling "network" drops the 2 resource templates backed by
    network-only operations (`get_elastic_ip`, `get_private_ip`) -- but
    every other tools/resources/prompts fetch for cmp-admin must keep
    working, not 500 or 400 across the board just because one domain is
    off. See `cmp_admin_mcp/resources.py::build_resource_templates`."""
    response = client.put(
        "/api/spec-sources/built-in/network", data={"server": "cmp-admin", "enabled": "false"}
    )
    assert response.status_code == 200

    tools = client.get("/api/servers/cmp-admin/tools")
    assert tools.status_code == 200

    resources = client.get("/api/servers/cmp-admin/resources")
    assert resources.status_code == 200
    uri_templates = {entry["uri_template"] for entry in resources.json()}
    assert "cmp://elastic-ip/{elastic_ip_id}" not in uri_templates
    assert "cmp://private-ip/{private_ip_id}" not in uri_templates
    assert "cmp://compute-node/{compute_id}" in uri_templates  # server domain, untouched
    assert "cmp://runbook/{pattern_id}" in uri_templates  # never operation-backed

    prompts = client.get("/api/servers/cmp-admin/prompts")
    assert prompts.status_code == 200


def test_server_v1_ships_disabled_and_is_free_to_toggle_alongside_server(client: TestClient) -> None:
    """`server_v1` (the legacy /admin-api surface) is a real shipped domain,
    off by default, that never collides with `server` (admin-v2) since they
    use disjoint operationId namespaces -- so both toggling it on alongside
    `server`, and swapping it in for `server`, both just work."""
    default_catalog = client.get("/api/servers/cmp-admin/tools/all")
    assert default_catalog.status_code == 200
    default_ids = {entry["operation_id"] for entry in default_catalog.json()}
    assert "admin_api_servers_retrieve" not in default_ids  # v1 not active by default
    assert "rebuild_server" in default_ids

    # Enable v1 alongside v2 -- both active, no collision.
    response = client.put(
        "/api/spec-sources/built-in/server_v1", data={"server": "cmp-admin", "enabled": "true"}
    )
    assert response.status_code == 200
    both_active = client.get("/api/servers/cmp-admin/tools/all")
    assert both_active.status_code == 200
    both_ids = {entry["operation_id"] for entry in both_active.json()}
    assert "admin_api_servers_retrieve" in both_ids
    assert "rebuild_server" in both_ids

    # Switch: v1 only, v2 off.
    client.put("/api/spec-sources/built-in/server", data={"server": "cmp-admin", "enabled": "false"})
    v1_only = client.get("/api/servers/cmp-admin/tools/all")
    assert v1_only.status_code == 200
    v1_ids = {entry["operation_id"] for entry in v1_only.json()}
    assert "admin_api_servers_retrieve" in v1_ids
    assert "rebuild_server" not in v1_ids

    # The v1-only resource templates that need `server` operations gracefully
    # drop, without breaking anything else.
    resources = client.get("/api/servers/cmp-admin/resources")
    assert resources.status_code == 200
    uri_templates = {entry["uri_template"] for entry in resources.json()}
    assert "cmp://compute-node/{compute_id}" not in uri_templates
    assert "cmp://elastic-ip/{elastic_ip_id}" in uri_templates  # network, untouched


def test_server_v1_has_its_own_default_pinned_tools(client: TestClient) -> None:
    """Same convention as the other built-ins (server.json pins
    rebuild_server + the compute-node discovery pair): server_v1 pins its
    list operation and its primary recovery action (admin_api_servers_recreate,
    the real v1 equivalent of rebuild_server). The v1 retrieve operation is
    now a resource (cmp://server-v1/{server_id}) instead of a tool, so agents
    access server detail as a resource -- so once enabled, an agent sees the
    discovery list and recovery action by default."""
    client.put("/api/spec-sources/built-in/server_v1", data={"server": "cmp-admin", "enabled": "true"})

    discovery_tools = client.get("/api/servers/cmp-admin/tools")
    assert discovery_tools.status_code == 200
    pinned_names = {entry["name"] for entry in discovery_tools.json()}
    assert "admin_api_servers_list" in pinned_names
    assert "admin_api_servers_recreate" in pinned_names
    assert "admin_api_servers_destroy" not in pinned_names  # everything else stays search-only

    resources = client.get("/api/servers/cmp-admin/resources")
    assert resources.status_code == 200
    resource_uris = {entry["uri_template"] for entry in resources.json()}
    assert "cmp://server-v1/{server_id}" in resource_uris  # admin_api_servers_retrieve is now a resource

    catalog = {
        entry["operation_id"]: entry for entry in client.get("/api/servers/cmp-admin/tools/all").json()
    }
    assert catalog["admin_api_servers_recreate"]["pinned"] is True
    assert catalog["admin_api_servers_destroy"]["pinned"] is False


def test_editing_one_built_in_never_breaks_the_catalog_or_resources(client: TestClient) -> None:
    """The original bug report: editing/uploading for one domain must never
    silently drop the other 2 built-ins (`build_resource_templates` indexes
    a handful of their operations directly and fails loudly if they go
    missing -- see `cmp_admin_mcp/resources.py`)."""
    with open("mcp_servers/specs/network.json", "rb") as f:
        network_spec = f.read()
    with open("mcp_servers/annotations/network.json", "rb") as f:
        network_annotations = f.read()

    response = client.put(
        "/api/spec-sources/built-in/network",
        data={"server": "cmp-admin", "name": "my network"},
        files={
            "spec_file": ("network.json", network_spec, "application/json"),
            "annotations_file": ("network.json", network_annotations, "application/json"),
        },
    )
    assert response.status_code == 200

    catalog = client.get("/api/servers/cmp-admin/tools/all")
    assert catalog.status_code == 200
    by_id = {entry["operation_id"]: entry for entry in catalog.json()}
    assert len(by_id) > 100
    assert "rebuild_server" in by_id  # server domain, untouched

    resources = client.get("/api/servers/cmp-admin/resources")
    assert resources.status_code == 200
    assert len(resources.json()) == 6
