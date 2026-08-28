"""Tests for progressive tool discovery on the merged multi-spec server:
`list_tools` returns only the curated `pinned` subset plus the
`search_tools`/`get_tool_schema` meta-tools, while `call_tool` keeps
dispatching to any real operation by exact name -- the empirical regression
the whole design rests on.
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
async def test_list_tools_returns_only_pinned_operations_plus_meta_tools() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_LIST")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert {t.name for t in tools.tools} == _PINNED_WITHOUT_LEGACY | _META_TOOLS


@pytest.mark.asyncio
async def test_pinned_annotation_on_an_operation_absent_from_this_build_is_inert() -> None:
    """get_volume_legacy is pinned in annotations/block_storage.json, but this
    server build never wires it in as an operation -- it's opt-in via
    CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET, resolved at the cmp-admin
    cutover, not by build_multi_spec_server itself. A pinned flag on an
    operation that isn't part of the server has nothing to pin."""
    from mcp.shared.memory import create_connected_server_and_client_session

    annotations = load_annotations(_BLOCK_STORAGE_ANNOTATIONS)
    assert annotations["get_volume_legacy"].pinned is True

    server = _build_admin_server("TEST_DISCOVERY_LEGACY_ABSENT")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert "get_volume_legacy" not in {t.name for t in tools.tools}


@pytest.mark.asyncio
async def test_get_volume_legacy_is_pinned_once_wired_into_a_server_build() -> None:
    """Once the legacy operation is actually part of the operation set (as it
    will be for cmp-admin whenever CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET
    is set), its pinned flag takes effect like any other operation's."""
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

    assert {t.name for t in tools.tools} == _PINNED_WITHOUT_LEGACY | _META_TOOLS | {
        "get_volume_legacy"
    }


@pytest.mark.asyncio
async def test_non_pinned_tool_is_still_callable_via_call_tool() -> None:
    """The whole point of progressive discovery: unlisted is not uncallable.
    list_server_types is a real, unpinned operation."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = _build_admin_server("TEST_DISCOVERY_CALL_UNLISTED")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert "list_server_types" not in {t.name for t in tools.tools}

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
