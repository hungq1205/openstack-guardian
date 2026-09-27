"""Proof that cmp-admin/cmp-logs no longer log their own events.

2026-09-26: both servers are piped through `guardian-admin` as proxied
external connections now (see the top-level workspace CLAUDE.md's
"proxy-gateway" notes) -- dispatch logging/approval-gating moved entirely
to that one layer (`guardian_platform.admin_mcp.proxy`, whose own call path
wraps every proxied call in `instrument_dispatch` exactly once). This
server's own `_call_tool` handlers (`openapi_bridge.py`'s
`_register_discovery_tool_handlers`, `cmp_logs_mcp/server.py`'s own
`_call_tool`) are now raw, ungated dispatch with no `instrument_dispatch`
call site left at all -- calling a tool directly against a bare
`build_admin_server()`/`build_logs_server()` (as these unit tests do, with
no proxy in front of them) must produce zero `events` rows. This guards
against the exact double-logging regression the whole redesign was meant
to avoid: if an `instrument_dispatch` call ever creeps back into either
server's own handler, a proxied call would log twice.

The real end-to-end proof that logging/gating actually works once these
servers are proxied lives in the sibling guardian-platform project:
`tests/test_admin_mcp.py`'s proxied-tool-call tests (mocking
`call_proxied_tool`) and `tests/test_admin_gui_mcp_servers_router.py`'s
live-subprocess probe test prove the pipe itself; the real single-events-
row-per-call guarantee is `guardian_platform.telemetry.instrument_dispatch`'s
own contract, unit-tested in that project's `test_shared_telemetry.py`,
now exercised exactly once per call -- at the proxy, not here.

Unit-level coverage of `instrument_dispatch` itself (masking, error
classification, exception re-raising, broken-sink resilience) lives in
guardian-platform's `test_shared_telemetry.py`, not duplicated here.
"""

from __future__ import annotations

import pytest
from guardian_platform import db
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.cmp_admin_mcp.main import build_admin_server
from mcp_servers.cmp_logs_mcp.server import build_server as build_logs_server


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """None of these tests should ever be able to reach a real CMP/ES
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
async def test_cmp_admin_tool_call_logs_nothing_without_the_proxy_in_front() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("list_compute_nodes", {})

    assert result.isError is False  # not_configured, no base URL in tests -- but still dispatched
    assert _events() == []


@pytest.mark.asyncio
async def test_cmp_admin_meta_tool_call_logs_nothing() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search_tools", {"query": "server"})

    assert _events() == []


@pytest.mark.asyncio
async def test_cmp_logs_search_call_logs_nothing() -> None:
    server = build_logs_server()
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search_logs", {"query": "srv-1"})

    assert _events() == []
