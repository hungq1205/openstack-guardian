"""End-to-end proof that the six instrumentation call sites wired in
`openapi_bridge.py`, `prompts/__init__.py`, `cmp_logs_mcp/server.py`, and
`cmp_notify_mcp/server.py` actually produce `events` rows for real MCP
protocol calls -- a tool call, a resource read, and a prompt fetch on
cmp-admin, plus one call each on cmp-logs and cmp-notify.

Unit-level coverage of `instrument_dispatch` itself (masking, error
classification, exception re-raising, broken-sink resilience) already lives
in `test_shared_telemetry.py`; this file only proves the wiring, not the
helper's own behavior again.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.cmp_admin_mcp.main import build_admin_server
from mcp_servers.cmp_logs_mcp.server import build_server as build_logs_server
from mcp_servers.cmp_notify_mcp.server import build_server as build_notify_server
from mcp_servers.shared import db


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """None of these tests should ever be able to reach a real CMP/ES/webhook
    endpoint, regardless of what a developer's shell happens to have set."""
    for var in (
        "CMP_ADMIN_V2_BASE_URL",
        "CMP_ADMIN_V2_PAT",
        "CMP_ADMIN_V2_USERNAME",
        "CMP_ADMIN_V2_PASSWORD",
        "CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET",
        "CMP_LOGS_ES_URL",
        "CMP_LOGS_ES_INDEX",
        "CMP_LOGS_ES_API_KEY",
        "CMP_LOGS_ES_USERNAME",
        "CMP_LOGS_ES_PASSWORD",
        "CMP_NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _events() -> list[dict[str, object]]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_cmp_admin_tool_call_is_logged() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("list_compute_nodes", {})

    events = [e for e in _events() if e["kind"] == "tool" and e["name"] == "list_compute_nodes"]
    assert len(events) == 1
    assert events[0]["server"] == "cmp-admin"
    assert events[0]["status"] == "error"  # not_configured, no base URL in tests
    assert events[0]["action"] is not None
    assert events[0]["action"].startswith("GET ")


@pytest.mark.asyncio
async def test_cmp_admin_resource_read_is_logged() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.read_resource("cmp://compute-node/abc-123")  # type: ignore[arg-type]

    events = [e for e in _events() if e["kind"] == "resource"]
    assert len(events) == 1
    assert events[0]["name"] == "cmp://compute-node/abc-123"
    assert events[0]["server"] == "cmp-admin"
    assert events[0]["action"] is not None
    assert events[0]["action"].startswith("GET ")


@pytest.mark.asyncio
async def test_cmp_admin_meta_tool_call_logs_no_action() -> None:
    """`search_tools` is a local catalog lookup, not a real endpoint call --
    unlike a real operation, it has no `GET`/`POST ...` to show."""
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search_tools", {"query": "server"})

    events = [e for e in _events() if e["kind"] == "tool" and e["name"] == "search_tools"]
    assert len(events) == 1
    assert events[0]["action"] is None


@pytest.mark.asyncio
async def test_cmp_admin_runbook_resource_read_logs_no_action() -> None:
    """The runbook resource resolves from an in-memory knowledge base, not a
    real GET -- it should log no action, unlike an operation-backed resource."""
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.read_resource("cmp://runbook/volume_status_drift")  # type: ignore[arg-type]

    events = [e for e in _events() if e["kind"] == "resource"]
    assert len(events) == 1
    assert events[0]["action"] is None


@pytest.mark.asyncio
async def test_cmp_admin_prompt_fetch_is_logged() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.get_prompt("assemble_log", {"server_id": "srv-1"})

    events = [e for e in _events() if e["kind"] == "prompt"]
    assert len(events) == 1
    assert events[0]["name"] == "assemble_log"
    assert events[0]["status"] == "success"
    assert events[0]["action"] is None  # purely local -- no real endpoint or command runs


@pytest.mark.asyncio
async def test_cmp_admin_unknown_prompt_still_raises_and_logs_an_error() -> None:
    from mcp import McpError

    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        with pytest.raises(McpError):
            await session.get_prompt("bogus_prompt", {})

    events = [e for e in _events() if e["kind"] == "prompt"]
    assert len(events) == 1
    assert events[0]["status"] == "error"


@pytest.mark.asyncio
async def test_cmp_logs_search_call_is_logged() -> None:
    server = build_logs_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search_logs", {"query": "srv-1"})

    events = [e for e in _events() if e["kind"] == "tool"]
    assert len(events) == 1
    assert events[0]["server"] == "cmp-logs"
    assert events[0]["name"] == "search_logs"
    assert events[0]["status"] == "error"  # not_configured, no ES URL in tests
    assert events[0]["action"] is None  # no ES URL/index configured in tests


@pytest.mark.asyncio
async def test_cmp_notify_call_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """notify_admin is always approval-gated (see test_shared_telemetry.py for
    that behavior itself) -- bypassing the wait here keeps this test focused
    on what it's actually proving: that a real call produces a correctly
    shaped `events` row."""
    from mcp_servers.shared import telemetry

    async def _auto_approve(_event_id: int) -> str:
        return "approved"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_approve)

    server = build_notify_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool(
            "notify_admin",
            {"server_id": "srv-1", "root_cause": "disk full", "reasoning": "needs a human"},
        )

    events = [e for e in _events() if e["kind"] == "tool"]
    assert len(events) == 1
    assert events[0]["server"] == "cmp-notify"
    assert events[0]["name"] == "notify_admin"
    assert events[0]["status"] == "error"  # not_configured, no webhook URL in tests
    assert events[0]["action"] is None  # no webhook URL configured in tests
