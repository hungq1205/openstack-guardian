"""Tests for tool discovery on the merged multi-spec server: `search_tools`/
`get_tool_schema` let an agent find/inspect a specific operation by keyword
or exact name. `list_tools` itself no longer filters by the old curated
`hidden` field (removed 2026-09-26 -- this server is piped through
`guardian-admin` as a proxied external connection now, and enable/disable is
the unified tool registry's job, applied one layer up by
`guardian_platform.admin_mcp.proxy`) -- it returns every real operation plus
the 2 meta-tools, unfiltered. `call_tool` keeps dispatching to any real
operation by exact name regardless.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.lowlevel import Server

from mcp_servers.cmp_admin_mcp.legacy_operations import LEGACY_OPERATIONS
from mcp_servers.openapi_bridge import (
    SpecSource,
    ToolAnnotation,
    build_multi_spec_server,
    load_annotations,
)

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"
_SERVER_ANNOTATIONS = _ANNOTATIONS_DIR / "server.json"
_BLOCK_STORAGE_ANNOTATIONS = _ANNOTATIONS_DIR / "block_storage.json"
_NETWORK_ANNOTATIONS = _ANNOTATIONS_DIR / "network.json"
_ALL_SOURCES = [
    SpecSource(_SPECS_DIR / "server.json", _SERVER_ANNOTATIONS),
    SpecSource(_SPECS_DIR / "block_storage.json", _BLOCK_STORAGE_ANNOTATIONS),
    SpecSource(_SPECS_DIR / "network.json", _NETWORK_ANNOTATIONS),
]
_PINNED_WITHOUT_LEGACY = {
    "rebuild_server",
    "list_compute_nodes",
}
_META_TOOLS = {"search_tools", "get_tool_schema"}


def _build_admin_server(env_prefix: str) -> Server:
    return build_multi_spec_server("cmp-admin", _ALL_SOURCES, env_prefix=env_prefix)


def _merged_annotations() -> dict[str, ToolAnnotation]:
    merged: dict[str, ToolAnnotation] = {}
    for path in (_SERVER_ANNOTATIONS, _BLOCK_STORAGE_ANNOTATIONS, _NETWORK_ANNOTATIONS):
        merged.update(load_annotations(path))
    return merged


@pytest.mark.asyncio
async def test_list_tools_returns_every_operation_plus_meta_tools() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_LIST")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    names = {t.name for t in tools.tools}
    assert _PINNED_WITHOUT_LEGACY <= names
    assert _META_TOOLS <= names
    assert "list_server_types" in names  # a real, non-"pinned" operation -- no longer excluded
    assert len(names) == 104 + len(_META_TOOLS)


@pytest.mark.asyncio
async def test_zero_curation_operations_default_to_visible() -> None:
    """Inverts test_list_tools_returns_only_pinned_operations_plus_meta_tools:
    a spec source with *no* annotations file at all -- not merely an
    operation missing its own entry in an existing file, but no curation
    source whatsoever -- now defaults every operation to visible under the
    opt-out `hidden` model. This is the exact opposite of the old opt-in
    `pinned` default, and the single highest-risk regression the
    pinned->hidden migration could have caused if a curated JSON file (or a
    tool_annotation_overrides row) were ever missing --
    see mcp_servers/cmp_admin_mcp/migrations/backfill_tool_visibility.py."""
    from mcp.shared.memory import create_connected_server_and_client_session

    uncurated_source = SpecSource(_SPECS_DIR / "network.json")  # no annotations_path at all
    server = build_multi_spec_server(
        "cmp-admin", [uncurated_source], env_prefix="TEST_DISCOVERY_ZERO_CURATION"
    )
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    names = {t.name for t in tools.tools}
    # Every network.json operation is visible, not a curated-down subset.
    assert len(names) > 20
    assert "create_floating_ip" in names


@pytest.mark.asyncio
async def test_pinned_annotation_on_an_operation_absent_from_this_build_is_inert() -> None:
    """get_volume_legacy is visible (hidden=False) in annotations/block_storage.json,
    but this server build never wires it in as an operation -- it's opt-in via
    CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET, resolved at the cmp-admin
    cutover, not by build_multi_spec_server itself. A visibility flag on an
    operation that isn't part of the server has nothing to show."""
    from mcp.shared.memory import create_connected_server_and_client_session

    annotations = load_annotations(_BLOCK_STORAGE_ANNOTATIONS)
    assert annotations["get_volume_legacy"].hidden is False

    server = _build_admin_server("TEST_DISCOVERY_LEGACY_ABSENT")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert "get_volume_legacy" not in {t.name for t in tools.tools}


@pytest.mark.asyncio
async def test_get_volume_legacy_appears_once_wired_into_a_server_build() -> None:
    """Once the legacy operation is actually part of the operation set (as it
    will be for cmp-admin whenever CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET
    is set), it shows up in `list_tools()` like any other real operation --
    the previous test proves it's genuinely absent when not wired in at all,
    unrelated to any visibility flag."""
    from mcp.shared.memory import create_connected_server_and_client_session

    sources = [
        _ALL_SOURCES[0],
        SpecSource(
            _SPECS_DIR / "block_storage.json",
            _BLOCK_STORAGE_ANNOTATIONS,
            extra_operations=LEGACY_OPERATIONS,
        ),
        _ALL_SOURCES[2],
    ]
    server = build_multi_spec_server("cmp-admin", sources, env_prefix="TEST_DISCOVERY_LEGACY_ON")

    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert "get_volume_legacy" in {t.name for t in tools.tools}


@pytest.mark.asyncio
async def test_non_pinned_tool_is_callable_via_call_tool() -> None:
    """list_server_types is real and callable by exact name regardless of
    whether an admin has ever curated an annotation for it."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_CALL_UNLISTED")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("list_server_types", {})

    assert result.isError is False
    assert result.structuredContent == {
        "error": "not_configured",
        "message": "list_server_types: no base URL configured",
    }


@pytest.mark.asyncio
async def test_search_tools_finds_relevant_operation_by_keyword() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_SEARCH_KEYWORD")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("search_tools", {"query": "resize server", "limit": 50})

    assert result.isError is False
    assert result.structuredContent is not None
    names = {item["operation_id"] for item in result.structuredContent["results"]}
    assert "resize_server" in names


@pytest.mark.asyncio
async def test_search_tools_filters_by_category() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    expected = {
        op_id for op_id, entry in _merged_annotations().items() if entry.tool_category == "admin"
    }
    assert expected

    server = _build_admin_server("TEST_DISCOVERY_SEARCH_CATEGORY")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("search_tools", {"category": "admin", "limit": 50})

    assert result.isError is False
    names = {item["operation_id"] for item in result.structuredContent["results"]}
    assert names == expected


@pytest.mark.asyncio
async def test_search_tools_filters_by_read_only() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_SEARCH_READONLY")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("search_tools", {"read_only": True, "limit": 50})

    assert result.isError is False
    results = result.structuredContent["results"]
    names = {item["operation_id"] for item in results}
    assert "get_compute_node" in names
    assert "delete_server" not in names
    assert all(item["read_only"] is True for item in results)


@pytest.mark.asyncio
async def test_search_tools_respects_limit_and_reports_total_matches() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_SEARCH_LIMIT")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("search_tools", {"limit": 1})

    assert result.isError is False
    body = result.structuredContent
    assert body["total_matches"] == 104
    assert body["returned"] == 1
    assert len(body["results"]) == 1


@pytest.mark.asyncio
async def test_search_tools_empty_query_defaults_to_matching_everything() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_SEARCH_DEFAULT")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("search_tools", {})

    assert result.isError is False
    body = result.structuredContent
    assert body["total_matches"] == 104
    assert body["returned"] == 20


@pytest.mark.asyncio
async def test_get_tool_schema_returns_full_definition_for_an_unlisted_operation() -> None:
    """list_server_types is real and has a genuine outputSchema (see
    test_openapi_bridge.py) but isn't pinned -- get_tool_schema must still
    return its full, real definition by exact name."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_GET_SCHEMA")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("get_tool_schema", {"operation_id": "list_server_types"})

    assert result.isError is False
    body = result.structuredContent
    assert body["name"] == "list_server_types"
    assert body["inputSchema"]["type"] == "object"
    assert body["outputSchema"] is not None
    assert body["annotations"]["readOnlyHint"] is True


@pytest.mark.asyncio
async def test_get_tool_schema_returns_error_for_unknown_operation() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_GET_SCHEMA_UNKNOWN")
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "get_tool_schema", {"operation_id": "not_a_real_operation"}
        )

    assert result.isError is False
    assert result.structuredContent == {"error": "unknown_tool", "tool": "not_a_real_operation"}
