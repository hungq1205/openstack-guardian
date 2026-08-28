"""Tests for `mcp_servers.shared.tickets`: the session-based ticket state
machine, and its wiring into `instrument_dispatch` -- proving a real dispatch
call gets attributed to whichever ticket is open for the current
`CLAUDE_CODE_SESSION_ID`, with zero argument threading through the call
itself. The admin GUI's own decision endpoints (which call `on_decision` at
the moment an operator actually decides) are covered end to end in
`test_admin_gui_events_router.py`; this file covers the state machine itself
plus the dispatch-side half of the wiring.
"""

from __future__ import annotations

import pytest

from mcp_servers.shared import db, tickets
from mcp_servers.shared.telemetry import instrument_dispatch


def _ticket_row(ticket_id: int) -> dict[str, object]:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    return dict(row)


def test_open_ticket_requires_a_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit delenv, not just an assumption of absence -- this suite is
    # itself commonly run from inside a real Claude Code session, which sets
    # this exact env var for its own subprocesses (verified empirically
    # during this feature's design).
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = tickets.open_ticket("investigate something")
    assert result == {
        "error": "no_session",
        "message": "CLAUDE_CODE_SESSION_ID is not set in this process's environment",
    }


def test_open_ticket_creates_an_investigating_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-1")
    ticket = tickets.open_ticket("investigate server abc-123")
    assert ticket["title"] == "investigate server abc-123"
    assert ticket["state"] == "investigating"
    assert ticket["resource_id"] is None
    row = _ticket_row(ticket["id"])
    assert row["session_id"] == "session-1"


def test_open_ticket_is_idempotent_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-2")
    first = tickets.open_ticket("first title")
    second = tickets.open_ticket("a different title, same session")
    assert first["id"] == second["id"]
    assert _ticket_row(first["id"])["title"] == "first title"


def test_current_ticket_id_is_none_without_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert tickets.current_ticket_id() is None


def test_current_ticket_id_is_none_before_a_ticket_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-3")
    assert tickets.current_ticket_id() is None


def test_backfill_resource_id_fills_once_and_never_overwrites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-4")
    ticket = tickets.open_ticket("t")
    tickets.backfill_resource_id(ticket["id"], "srv-1")
    assert _ticket_row(ticket["id"])["resource_id"] == "srv-1"
    tickets.backfill_resource_id(ticket["id"], "srv-2")
    assert _ticket_row(ticket["id"])["resource_id"] == "srv-1"


def test_on_dispatched_bumps_planned_to_resolving_on_first_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-5")
    ticket = tickets.open_ticket("t")
    tickets.on_decision(ticket["id"], "submit_investigation_plan", "approved")
    assert _ticket_row(ticket["id"])["state"] == "planned"

    tickets.on_dispatched(ticket["id"], "rebuild_server", True)
    assert _ticket_row(ticket["id"])["state"] == "resolving"


def test_on_dispatched_ignores_read_only_calls_and_lifecycle_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-6")
    ticket = tickets.open_ticket("t")
    tickets.on_decision(ticket["id"], "submit_investigation_plan", "approved")

    tickets.on_dispatched(ticket["id"], "search_logs", False)
    assert _ticket_row(ticket["id"])["state"] == "planned"

    tickets.on_dispatched(ticket["id"], "submit_investigation_report", True)
    assert _ticket_row(ticket["id"])["state"] == "planned"


def test_on_pending_report_moves_ticket_to_in_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-7")
    ticket = tickets.open_ticket("t")
    tickets.on_pending(ticket["id"], "submit_investigation_report")
    assert _ticket_row(ticket["id"])["state"] == "in_review"


@pytest.mark.parametrize(
    ("name", "decision", "expected_state", "expect_closed"),
    [
        ("submit_investigation_plan", "approved", "planned", False),
        ("submit_investigation_plan", "changes_requested", "investigating", False),
        ("submit_investigation_plan", "denied", "escalated", True),
        ("submit_investigation_report", "approved", "completed", True),
        ("submit_investigation_report", "changes_requested", "resolving", False),
        ("submit_investigation_report", "denied", "escalated", True),
        ("notify_admin", "approved", "escalated", True),
    ],
)
def test_on_decision_transition_matrix(
    monkeypatch: pytest.MonkeyPatch, name: str, decision: str, expected_state: str, expect_closed: bool
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", f"session-matrix-{name}-{decision}")
    ticket = tickets.open_ticket("t")

    tickets.on_decision(ticket["id"], name, decision)

    row = _ticket_row(ticket["id"])
    assert row["state"] == expected_state
    assert (row["closed_at"] is not None) is expect_closed


def test_on_decision_ignores_unknown_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-8")
    ticket = tickets.open_ticket("t")
    tickets.on_decision(ticket["id"], "some_other_tool", "approved")
    assert _ticket_row(ticket["id"])["state"] == "investigating"


def test_on_decision_is_a_no_op_without_a_ticket() -> None:
    # Must not raise even though ticket_id is None -- e.g. a call made
    # outside any open investigation.
    tickets.on_decision(None, "submit_investigation_plan", "approved")


@pytest.mark.asyncio
async def test_instrument_dispatch_attributes_events_to_the_open_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual point of this design: a real dispatch call carries no
    ticket_id argument at all, yet still lands in the right ticket's log,
    purely from the process's own CLAUDE_CODE_SESSION_ID."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-9")
    ticket = tickets.open_ticket("investigate something")

    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="search_logs",
        arguments={"query": "srv-1"},
        source="cmp-admin.tool.search_logs",
        dispatch=_dispatch,
    )

    conn = db.connect()
    try:
        row = conn.execute("SELECT ticket_id FROM events ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["ticket_id"] == ticket["id"]


@pytest.mark.asyncio
async def test_instrument_dispatch_leaves_events_unassigned_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    async def _dispatch() -> dict[str, object]:
        return {"status_code": 200, "data": {}}

    await instrument_dispatch(
        server="cmp-admin",
        kind="tool",
        name="search_logs",
        arguments={},
        source="cmp-admin.tool.search_logs",
        dispatch=_dispatch,
    )

    conn = db.connect()
    try:
        row = conn.execute("SELECT ticket_id FROM events ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["ticket_id"] is None
