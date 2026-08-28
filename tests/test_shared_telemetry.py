"""Tests for `mcp_servers.shared.telemetry.instrument_dispatch`: the one
helper wrapped around all six real call-dispatch sites across the three MCP
servers. Covers success/error classification, masking before persistence,
exception re-raising (critical for `get_prompt`'s documented ValueError
behavior), and that a broken log sink never breaks the wrapped call.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_servers.shared import db, telemetry
from mcp_servers.shared.telemetry import instrument_dispatch


def _events() -> list[dict[str, object]]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_logs_a_success_event() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {"ok": True}}

    result = await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="list_compute_nodes",
        arguments={"page_size": 10},
        source="cmp-admin.tool.list_compute_nodes",
        dispatch=_dispatch,
    )

    assert result == {"status_code": 200, "data": {"ok": True}}
    events = _events()
    assert len(events) == 1
    assert events[0]["status"] == "success"
    assert events[0]["server"] == "cmp-admin"
    assert events[0]["kind"] == "tool"
    assert events[0]["name"] == "list_compute_nodes"
    assert events[0]["error_message"] is None


@pytest.mark.asyncio
async def test_logs_the_action_when_provided() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="get_compute_node",
        arguments={"compute_id": "node-1"},
        source="cmp-admin.tool.get_compute_node",
        dispatch=_dispatch,
        action="GET /admin-v2/compute-nodes/{compute_id}/",
    )

    events = _events()
    assert events[0]["action"] == "GET /admin-v2/compute-nodes/{compute_id}/"


@pytest.mark.asyncio
async def test_action_defaults_to_none_when_not_provided() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    await instrument_dispatch(
        server="cmp-admin",
        kind="prompt",
        name="assemble_log",
        arguments={},
        source="cmp-admin.prompt.assemble_log",
        dispatch=_dispatch,
    )

    events = _events()
    assert events[0]["action"] is None


@pytest.mark.asyncio
async def test_logs_an_error_event_from_the_error_envelope() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"error": "not_configured", "message": "no base URL configured"}

    result = await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="rebuild_server",
        arguments={},
        source="cmp-admin.tool.rebuild_server",
        dispatch=_dispatch,
    )

    assert result["error"] == "not_configured"
    events = _events()
    assert events[0]["status"] == "error"
    assert events[0]["error_message"] == "no base URL configured"


@pytest.mark.asyncio
async def test_reraises_exceptions_unchanged_after_logging() -> None:
    async def _dispatch() -> dict[str, object]:
        raise ValueError("Unknown prompt: bogus")

    with pytest.raises(ValueError, match="Unknown prompt: bogus"):
        await instrument_dispatch(
            server="cmp-admin",
            kind="prompt",
            name="bogus",
            arguments={},
            source="cmp-admin.prompt.bogus",
            dispatch=_dispatch,
        )

    events = _events()
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert events[0]["error_message"] == "Unknown prompt: bogus"


@pytest.mark.asyncio
async def test_masks_arguments_and_result_before_persisting() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {"contact": "admin@example.com"}}

    await instrument_dispatch(
        server="cmp-logs",
        kind="tool",
        name="search_logs",
        arguments={"note": "cc jane@example.com"},
        source="cmp-logs.tool.search_logs",
        dispatch=_dispatch,
    )

    events = _events()
    assert "jane@example.com" not in str(events[0]["arguments_json"])
    assert "admin@example.com" not in str(events[0]["result_summary"])
    assert "[REDACTED:EMAIL]" in str(events[0]["arguments_json"])
    assert "[REDACTED:EMAIL]" in str(events[0]["result_summary"])


def _set_status(event_id: object, status: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_requires_approval_blocks_dispatch_until_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_APPROVAL_POLL_SECONDS", 0.01)
    dispatched = False

    async def _dispatch() -> dict[str, object]:
        nonlocal dispatched
        dispatched = True
        return {"status_code": 202, "data": {}}

    task = asyncio.create_task(
        instrument_dispatch(
            server="cmp-admin",
            kind="tool",
            name="rebuild_server",
            arguments={"server_id": "srv-1"},
            source="cmp-admin.tool.rebuild_server",
            dispatch=_dispatch,
            requires_approval=True,
        )
    )
    await asyncio.sleep(0.05)
    assert not dispatched
    pending = _events()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"

    _set_status(pending[0]["id"], "approved")
    result = await asyncio.wait_for(task, timeout=5)

    assert dispatched
    assert result == {"status_code": 202, "data": {}}
    events = _events()
    assert len(events) == 1
    assert events[0]["status"] == "success"


@pytest.mark.asyncio
async def test_requires_approval_denied_never_runs_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_APPROVAL_POLL_SECONDS", 0.01)
    dispatched = False

    async def _dispatch() -> dict[str, object]:
        nonlocal dispatched
        dispatched = True
        return {"status_code": 202, "data": {}}

    task = asyncio.create_task(
        instrument_dispatch(
            server="cmp-admin",
            kind="tool",
            name="delete_server",
            arguments={},
            source="cmp-admin.tool.delete_server",
            dispatch=_dispatch,
            requires_approval=True,
        )
    )
    await asyncio.sleep(0.05)
    event_id = _events()[0]["id"]

    _set_status(event_id, "denied")
    result = await asyncio.wait_for(task, timeout=5)

    assert not dispatched
    assert result["error"] == "denied_by_operator"
    events = _events()
    assert events[0]["status"] == "denied"
    assert events[0]["error_message"] is not None


@pytest.mark.asyncio
async def test_requires_approval_changes_requested_never_runs_dispatch_and_returns_the_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin GUI's request-changes action (see
    admin_gui/backend/routers/events.py) sets status=changes_requested and
    stashes its comment in error_message *before* this call ever wakes up --
    this pins that instrument_dispatch reads that comment back out and hands
    it to the caller as the tool result, rather than treating it as a flat
    denial."""
    monkeypatch.setattr(telemetry, "_APPROVAL_POLL_SECONDS", 0.01)
    dispatched = False

    async def _dispatch() -> dict[str, object]:
        nonlocal dispatched
        dispatched = True
        return {"status": "plan_approved"}

    task = asyncio.create_task(
        instrument_dispatch(
            server="cmp-admin",
            kind="tool",
            name="submit_investigation_plan",
            arguments={},
            source="cmp-admin.tool.submit_investigation_plan",
            dispatch=_dispatch,
            requires_approval=True,
        )
    )
    await asyncio.sleep(0.05)
    event_id = _events()[0]["id"]

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE events SET status = 'changes_requested', error_message = ? WHERE id = ?",
            ("try force=true instead", event_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = await asyncio.wait_for(task, timeout=5)

    assert not dispatched
    assert result == {"error": "changes_requested", "message": "try force=true instead"}
    events = _events()
    assert events[0]["status"] == "changes_requested"
    assert events[0]["error_message"] == "try force=true instead"


@pytest.mark.asyncio
async def test_requires_approval_false_skips_the_pending_state_entirely() -> None:
    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="cancel_scan",
        arguments={},
        source="cmp-admin.tool.cancel_scan",
        dispatch=_dispatch,
    )
    events = _events()
    assert len(events) == 1
    assert events[0]["status"] == "success"


@pytest.mark.asyncio
async def test_never_raises_when_the_log_sink_itself_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("mcp_servers.shared.telemetry.db.connect", _raise)

    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    result = await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="list_compute_nodes",
        arguments={},
        source="cmp-admin.tool.list_compute_nodes",
        dispatch=_dispatch,
    )
    assert result == {"status_code": 200, "data": {}}
