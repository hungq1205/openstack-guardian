"""Tests for cmp-admin's resource templates: list_resources/list_resource_templates/
read_resource wiring, the capability-gating regression the plan calls out
explicitly (a server only advertises `resources` once list_resources is
registered, independent of the other two handlers), and both backing
mechanisms in use here -- a real GET operation, and the in-memory
failure-pattern knowledge base for cmp://runbook/{pattern_id}.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import NotificationOptions
from pydantic import AnyUrl

from mcp_servers.cmp_admin_mcp.resources import build_resource_templates
from mcp_servers.openapi_bridge import (
    CmpApiClient,
    ResourceTemplateSpec,
    SpecSource,
    load_annotations,
    merge_operations,
    register_resource_templates,
)

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"
_ALL_SOURCES = [
    SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json"),
    SpecSource(_SPECS_DIR / "block_storage.json", _ANNOTATIONS_DIR / "block_storage.json"),
    SpecSource(_SPECS_DIR / "network.json", _ANNOTATIONS_DIR / "network.json"),
]
_ALL_URI_TEMPLATES = {
    "cmp://compute-node/{compute_id}",
    "cmp://host-aggregate/{aggregate_id}",
    "cmp://server-compute-node/{server_id}",
    "cmp://elastic-ip/{elastic_ip_id}",
    "cmp://private-ip/{private_ip_id}",
    "cmp://runbook/{pattern_id}",
}


def _admin_resource_templates(env_prefix: str) -> list[ResourceTemplateSpec]:
    operations = merge_operations(_ALL_SOURCES)
    annotations = {}
    for source in _ALL_SOURCES:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    client = CmpApiClient.from_env(env_prefix)
    return build_resource_templates(operations, annotations, client)


def test_resources_capability_is_not_advertised_without_list_resources() -> None:
    """The capability-gating regression: a bare server with no resource
    handlers registered at all must not advertise the resources capability."""
    server: Server = Server("bare")
    capabilities = server.get_capabilities(NotificationOptions(), {})
    assert capabilities.resources is None


def test_resources_capability_is_advertised_once_list_resources_is_registered() -> None:
    server: Server = Server("with-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_CAPABILITY"))
    capabilities = server.get_capabilities(NotificationOptions(), {})
    assert capabilities.resources is not None


@pytest.mark.asyncio
async def test_list_resources_is_always_empty() -> None:
    """No enumerable concrete resources exist here -- only templates."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_LIST"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_resources()

    assert result.resources == []


@pytest.mark.asyncio
async def test_list_resource_templates_returns_all_six_curated_templates() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_TEMPLATES"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_resource_templates()

    assert {t.uriTemplate for t in result.resourceTemplates} == _ALL_URI_TEMPLATES


@pytest.mark.asyncio
async def test_read_resource_reaches_the_real_operation_for_an_api_backed_template() -> None:
    """No base URL is configured, so a not_configured error -- rather than an
    unknown_resource_uri or a crash -- proves the read reached the real
    CmpApiClient/OperationSpec path via operation_resolver."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_READ_API"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.read_resource(AnyUrl("cmp://compute-node/abc-123"))

    body = json.loads(result.contents[0].text)
    assert body == {
        "error": "not_configured",
        "message": "get_compute_node: no base URL configured",
    }


@pytest.mark.asyncio
async def test_read_resource_returns_the_full_record_for_a_known_runbook_id() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_RUNBOOK"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.read_resource(AnyUrl("cmp://runbook/volume_status_drift"))

    body = json.loads(result.contents[0].text)
    assert body["id"] == "volume_status_drift"
    # instruction content is edited live through the admin GUI's
    # failure-pattern editor -- only pin that the resource resolves to
    # the right record with required fields present.
    assert "instruction" in body
    assert "signature_pattern" in body


@pytest.mark.asyncio
async def test_read_resource_returns_not_found_for_an_unknown_runbook_id() -> None:
    """Unlike the API-backed templates, the runbook resolver has a genuine
    'no such id' case -- a local dict lookup, not an HTTP call."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_RUNBOOK_MISSING"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.read_resource(AnyUrl("cmp://runbook/not_a_real_pattern"))

    body = json.loads(result.contents[0].text)
    assert body == {"error": "not_found", "uri": "cmp://runbook/not_a_real_pattern"}


@pytest.mark.asyncio
async def test_read_resource_returns_unknown_uri_for_a_uri_matching_no_template() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server: Server = Server("cmp-admin-resources")
    register_resource_templates(server, _admin_resource_templates("TEST_RESOURCES_UNKNOWN"))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.read_resource(AnyUrl("cmp://not-a-real-template/xyz"))

    body = json.loads(result.contents[0].text)
    assert body == {"error": "unknown_resource_uri", "uri": "cmp://not-a-real-template/xyz"}


def test_register_resource_templates_rejects_a_template_with_no_variable() -> None:
    server: Server = Server("bad-template")
    bad_spec = ResourceTemplateSpec(
        uri_template="cmp://compute-nodes",
        name="compute-nodes",
        description="missing its {variable}",
        resolver=lambda _value: None,
    )

    with pytest.raises(ValueError, match="single-variable"):
        register_resource_templates(server, [bad_spec])


def test_register_resource_templates_rejects_a_template_with_two_variables() -> None:
    server: Server = Server("bad-template")
    bad_spec = ResourceTemplateSpec(
        uri_template="cmp://thing/{a}/{b}",
        name="thing",
        description="has two variables",
        resolver=lambda _value: None,
    )

    with pytest.raises(ValueError, match="single-variable"):
        register_resource_templates(server, [bad_spec])


def test_build_resource_templates_adds_server_v1_when_that_domain_is_present() -> None:
    """`server-v1` is a real, full server-detail resource (unlike admin-v2's
    placement-only `server-compute-node`) -- present only when the
    `server_v1` domain's operations are in the merged set, e.g. once an
    admin enables it on the Spec Sources page."""
    sources_with_v1 = [*_ALL_SOURCES, SpecSource(_SPECS_DIR / "server_v1.json", _ANNOTATIONS_DIR / "server_v1.json")]
    operations = merge_operations(sources_with_v1)
    annotations = {}
    for source in sources_with_v1:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    client = CmpApiClient.from_env("TEST_RESOURCES_SERVER_V1")

    templates = build_resource_templates(operations, annotations, client)

    uri_templates = {t.uri_template for t in templates}
    assert uri_templates == _ALL_URI_TEMPLATES | {"cmp://server-v1/{server_id}"}


def test_build_resource_templates_skips_a_missing_operation_instead_of_raising() -> None:
    """The admin GUI's Spec Sources page can disable or replace one of the 3
    domains, dropping whatever operations it covered -- `network` covers
    both `get_elastic_ip` and `get_private_ip`. That must silently drop
    just those 2 templates, not raise a `KeyError` for the whole catalog."""
    sources_without_network = [
        SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json"),
        SpecSource(_SPECS_DIR / "block_storage.json", _ANNOTATIONS_DIR / "block_storage.json"),
    ]
    operations = merge_operations(sources_without_network)
    annotations = {}
    for source in sources_without_network:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    client = CmpApiClient.from_env("TEST_RESOURCES_MISSING_DOMAIN")

    templates = build_resource_templates(operations, annotations, client)

    uri_templates = {t.uri_template for t in templates}
    assert uri_templates == _ALL_URI_TEMPLATES - {
        "cmp://elastic-ip/{elastic_ip_id}",
        "cmp://private-ip/{private_ip_id}",
    }
