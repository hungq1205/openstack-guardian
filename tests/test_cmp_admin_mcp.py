"""Tests for the cmp-admin cutover itself: `build_admin_server`/`main.py`
wiring every piece built in the earlier migration steps -- tools (including
the progressive-discovery pinned set, search_tools/get_tool_schema, the
search_failure_patterns extra tool, and the start_investigate/
submit_investigation_plan/submit_investigation_report extra tools), resource
templates, and the assemble_log prompt -- onto one real Server instance,
plus the legacy get_volume_legacy opt-in this server now owns.

The deep-dive behavioral coverage for each piece already lives in its own
test module (test_admin_server_merge.py, test_tool_discovery.py,
test_failure_patterns.py, test_admin_resources.py, test_prompts.py); this
file only proves the assembly in main.py actually wires them together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers.cmp_admin_mcp.main import build_admin_server, legacy_operations_if_enabled
from mcp_servers.openapi_bridge import load_operations

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_PINNED_TOOLS = {
    "rebuild_server",
    "list_compute_nodes",
}
_META_TOOLS = {"search_tools", "get_tool_schema"}
_RESOURCE_URI_TEMPLATES = {
    "cmp://compute-node/{compute_id}",
    "cmp://host-aggregate/{aggregate_id}",
    "cmp://server-compute-node/{server_id}",
    "cmp://elastic-ip/{elastic_ip_id}",
    "cmp://private-ip/{private_ip_id}",
    "cmp://runbook/{pattern_id}",
}


@pytest.fixture(autouse=True)
def _clean_admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here builds a real cmp-admin server via build_admin_server,
    which reads the real CMP_ADMIN_V2_* env vars -- main.py hardcodes that
    prefix, unlike the other test modules' fake-env_prefix helpers. Clear
    them all so a developer's real configured credentials can never turn a
    test into a live network call against a real CMP instance."""
    for var in (
        "CMP_ADMIN_V2_BASE_URL",
        "CMP_ADMIN_V2_PAT",
        "CMP_ADMIN_V2_USERNAME",
        "CMP_ADMIN_V2_PASSWORD",
        "CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET",
    ):
        monkeypatch.delenv(var, raising=False)


def test_legacy_volume_get_is_off_by_default() -> None:
    assert legacy_operations_if_enabled() is None


def test_legacy_volume_get_opts_in_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET", "1")
    extra = legacy_operations_if_enabled()
    assert extra is not None
    assert extra["get_volume_legacy"].path == "/admin-api/volumes/{volume_id}/"


_INVESTIGATION_REPORTING_TOOLS = {"start_investigate", "submit_investigation_plan", "submit_investigation_report"}


@pytest.mark.asyncio
async def test_build_admin_server_lists_pinned_tools_plus_meta_plus_failure_patterns() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_admin_server()

    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert {t.name for t in tools.tools} == (
        _PINNED_TOOLS | _META_TOOLS | {"search_failure_patterns"} | _INVESTIGATION_REPORTING_TOOLS
    )


@pytest.mark.asyncio
async def test_build_admin_server_lists_get_volume_legacy_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    monkeypatch.setenv("CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET", "1")
    server = build_admin_server()

    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    assert "get_volume_legacy" in {t.name for t in tools.tools}


@pytest.mark.asyncio
async def test_build_admin_server_every_real_operation_is_still_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same proof as test_admin_server_merge.py, against the actual assembled
    server: a missing operation would come back unknown_tool. No base URL is
    configured (see _clean_admin_env), so every call resolves to
    not_configured rather than a real network request.

    Many of these operations are approval-gated action calls -- bypassing
    the wait here keeps this test about existence/dispatchability, not
    approval (see test_shared_telemetry.py for that behavior itself)."""
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp_servers.shared import telemetry

    async def _auto_approve(_event_id: int) -> str:
        return "approved"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_approve)

    expected = (
        set(load_operations(_SPECS_DIR / "server.json"))
        | set(load_operations(_SPECS_DIR / "block_storage.json"))
        | set(load_operations(_SPECS_DIR / "network.json"))
    )
    server = build_admin_server()

    async with create_connected_server_and_client_session(server) as session:
        for operation_id in expected:
            result = await session.call_tool(operation_id, {})
            assert result.structuredContent != {"error": "unknown_tool", "tool": operation_id}


@pytest.mark.asyncio
async def test_build_admin_server_exposes_all_six_resource_templates() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_admin_server()

    async with create_connected_server_and_client_session(server) as session:
        resources = await session.list_resources()
        templates = await session.list_resource_templates()

    assert resources.resources == []
    assert {t.uriTemplate for t in templates.resourceTemplates} == _RESOURCE_URI_TEMPLATES


@pytest.mark.asyncio
async def test_build_admin_server_exposes_assemble_log_prompt() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_admin_server()

    async with create_connected_server_and_client_session(server) as session:
        prompts = await session.list_prompts()

    assert [p.name for p in prompts.prompts] == ["assemble_log"]


@pytest.mark.asyncio
async def test_real_round_trip_through_the_live_mcp_subprocess() -> None:
    """The same live-subprocess proof every other server here uses: genuinely
    spawn `python -m mcp_servers.cmp_admin_mcp.main` (the exact command
    `.mcp.json` runs) and get real responses back over the real protocol."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_servers.cmp_admin_mcp.main"]
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resource_templates()
        prompts = await session.list_prompts()

    tool_names = {t.name for t in tools.tools}
    # Check that the meta-tools and search_failure_patterns are present
    assert _META_TOOLS | {"search_failure_patterns"} <= tool_names
    # Check that resources include the runbook at least
    resource_templates = {t.uriTemplate for t in resources.resourceTemplates}
    assert "cmp://runbook/{pattern_id}" in resource_templates
    # Check prompt is present
    assert [p.name for p in prompts.prompts] == ["assemble_log"]
