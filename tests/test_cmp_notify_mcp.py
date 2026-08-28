"""Tests for the cmp-notify MCP server: request construction, the honest
`not_configured` response, and a genuine live-subprocess round trip -- same
strategy as `test_cmp_logs_mcp.py`, since there's no real webhook endpoint to
test against here either.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest

from mcp_servers.cmp_notify_mcp.client import NotifyClient
from mcp_servers.cmp_notify_mcp.server import build_server
from mcp_servers.shared import db


async def _wait_for_pending_event(name: str, *, timeout: float = 5.0) -> int:
    """Poll the (test-isolated) events table for a 'pending' row by tool
    name -- notify_admin is always approval-gated now, so the live-subprocess
    round trip below has to approve it before the call can return."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id FROM events WHERE name = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return int(row["id"])
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"no pending event named {name!r} appeared within {timeout}s")
        await asyncio.sleep(0.05)


def _approve(event_id: int) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE events SET status = 'approved' WHERE id = ?", (event_id,))
        conn.commit()
    finally:
        conn.close()


def _client_with_transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_without_configuration_reports_not_configured() -> None:
    client = NotifyClient(webhook_url="")

    result = client.send(server_id="srv-1", root_cause="bad image", reasoning="needs a human")

    assert result == {
        "error": "not_configured",
        "message": "notify_admin: no webhook URL configured",
    }


def test_send_posts_the_incident_payload_to_the_webhook() -> None:
    client = NotifyClient(webhook_url="https://hooks.example.com/notify")
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"ok": True})

    result = client.send(
        server_id="srv-1",
        root_cause="image not active",
        reasoning="requires a customer/admin judgment call",
        evidence="log line X",
        client=_client_with_transport(handler),
    )

    request = captured["request"]
    assert str(request.url) == "https://hooks.example.com/notify"
    body = request.read()
    assert b'"server_id":"srv-1"' in body or b'"server_id": "srv-1"' in body
    assert b"image not active" in body
    assert b"log line X" in body
    assert result == {"notified": True, "status_code": 200}


def test_send_reports_a_failed_delivery_without_raising() -> None:
    client = NotifyClient(webhook_url="https://hooks.example.com/notify")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = client.send(
        server_id="srv-1",
        root_cause="x",
        reasoning="y",
        client=_client_with_transport(handler),
    )

    assert result == {"notified": False, "status_code": 500}


def test_build_server_name() -> None:
    server = build_server()
    assert server.name == "cmp-notify"


@pytest.mark.asyncio
async def test_list_tools_declares_notify_admin_as_non_destructive() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    assert [tool.name for tool in result.tools] == ["notify_admin"]
    annotations = result.tools[0].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_real_round_trip_through_the_live_mcp_subprocess() -> None:
    """The same live-subprocess proof `test_cmp_logs_mcp.py` uses: genuinely
    spawn `python -m mcp_servers.cmp_notify_mcp.main` (the exact command
    `.mcp.json` runs) and get a real response back."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.cmp_notify_mcp.main"],
        # Only a curated safe subset of the parent's env reaches the child by
        # default (see mcp.client.stdio.get_default_environment) -- explicitly
        # forwarding CMP_MCP_DATA_DIR keeps this subprocess writing to the same
        # test-isolated events table conftest.py points the parent process at,
        # instead of the real repo-root data/guardian.db.
        env={"CMP_MCP_DATA_DIR": os.environ["CMP_MCP_DATA_DIR"]},
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        call_task = asyncio.create_task(
            session.call_tool(
                "notify_admin",
                {"server_id": "srv-1", "root_cause": "x", "reasoning": "y"},
            )
        )
        event_id = await _wait_for_pending_event("notify_admin")
        _approve(event_id)
        result = await asyncio.wait_for(call_task, timeout=10)

    assert result.structuredContent == {
        "error": "not_configured",
        "message": "notify_admin: no webhook URL configured",
    }
