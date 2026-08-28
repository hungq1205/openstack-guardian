"""Tests for `/api/events`: filtered/paginated listing plus the live SSE
tail.

The tail's actual push behavior is tested by driving the real
`_event_stream()` async generator directly rather than through
`TestClient.stream()`. Confirmed empirically that the latter cannot be used
at all against this route, not even to read just the response headers:
`TestClient` runs on httpx's `ASGITransport`, which drives the whole ASGI
`send`/`receive` cycle to completion before handing anything back to the
caller -- for a genuinely infinite generator (a real live tail has no
natural end), that means it hangs forever, before a single byte is
observable. This is a real limitation of this test tool against this kind
of endpoint, not a bug in the endpoint; stated plainly rather than worked
around with a fake success. SSE wire-framing itself is `sse-starlette`'s
job and isn't re-tested here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from admin_gui.backend.routers import events as events_router
from mcp_servers.shared import tickets
from mcp_servers.shared.telemetry import log_event, log_pending_event


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _log(name: str, *, server: str = "cmp-admin", status: str = "success") -> None:
    log_event(
        server=server,
        kind="tool",
        name=name,
        arguments={},
        status=status,
        error_message=None if status == "success" else "boom",
        result_summary={},
        duration_ms=1.0,
    )


def test_list_events_is_empty_by_default(client: TestClient) -> None:
    response = client.get("/api/events")
    assert response.status_code == 200
    assert response.json() == []


def test_list_events_returns_newest_first(client: TestClient) -> None:
    _log("first_call")
    _log("second_call")
    response = client.get("/api/events")
    names = [entry["name"] for entry in response.json()]
    assert names == ["second_call", "first_call"]


def test_list_events_filters_by_server_kind_status(client: TestClient) -> None:
    _log("admin_call", server="cmp-admin", status="success")
    _log("logs_call", server="cmp-logs", status="error")

    by_server = client.get("/api/events", params={"server": "cmp-logs"}).json()
    assert [e["name"] for e in by_server] == ["logs_call"]

    by_status = client.get("/api/events", params={"status": "error"}).json()
    assert [e["name"] for e in by_status] == ["logs_call"]

    by_kind = client.get("/api/events", params={"kind": "tool"}).json()
    assert len(by_kind) == 2


def test_list_events_respects_limit(client: TestClient) -> None:
    for i in range(5):
        _log(f"call_{i}")
    response = client.get("/api/events", params={"limit": 2})
    assert len(response.json()) == 2


def test_list_events_filters_by_since_and_until(client: TestClient) -> None:
    _log("before_window")
    boundary = datetime.now(UTC)
    _log("in_window")

    by_since = client.get("/api/events", params={"since": boundary.isoformat()}).json()
    assert [e["name"] for e in by_since] == ["in_window"]

    by_until = client.get("/api/events", params={"until": boundary.isoformat()}).json()
    assert [e["name"] for e in by_until] == ["before_window"]


def test_list_events_rejects_unparseable_since(client: TestClient) -> None:
    response = client.get("/api/events", params={"since": "not-a-timestamp"})
    assert response.status_code == 400


def test_list_events_since_id_only_returns_newer_rows(client: TestClient) -> None:
    _log("old_call")
    first_id = client.get("/api/events").json()[0]["id"]
    _log("new_call")

    response = client.get("/api/events", params={"since_id": first_id})
    assert [e["name"] for e in response.json()] == ["new_call"]


def test_clear_events_deletes_everything_and_reports_the_count(client: TestClient) -> None:
    _log("first_call")
    _log("second_call")

    response = client.delete("/api/events")

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert client.get("/api/events").json() == []


def test_clear_events_on_an_empty_log_reports_zero(client: TestClient) -> None:
    response = client.delete("/api/events")

    assert response.status_code == 200
    assert response.json() == {"deleted": 0}


def test_approve_pending_event(client: TestClient) -> None:
    event_id = log_pending_event(server="cmp-admin", kind="tool", name="rebuild_server", arguments={})
    response = client.post(f"/api/events/{event_id}/approve")
    assert response.status_code == 200
    assert response.json() == {"status": "approved"}


def test_deny_pending_event(client: TestClient) -> None:
    event_id = log_pending_event(server="cmp-admin", kind="tool", name="delete_server", arguments={})
    response = client.post(f"/api/events/{event_id}/deny")
    assert response.status_code == 200
    assert response.json() == {"status": "denied"}


def test_decide_unknown_event_is_404(client: TestClient) -> None:
    response = client.post("/api/events/999999/approve")
    assert response.status_code == 404


def test_decide_an_already_decided_event_is_409(client: TestClient) -> None:
    event_id = log_pending_event(server="cmp-admin", kind="tool", name="rebuild_server", arguments={})
    client.post(f"/api/events/{event_id}/approve")
    response = client.post(f"/api/events/{event_id}/deny")
    assert response.status_code == 409


def test_request_changes_on_pending_event(client: TestClient) -> None:
    event_id = log_pending_event(
        server="cmp-admin", kind="tool", name="submit_investigation_plan", arguments={}
    )
    response = client.post(f"/api/events/{event_id}/request-changes", json={"comment": "try force=true instead"})
    assert response.status_code == 200
    assert response.json() == {"status": "changes_requested"}

    row = client.get("/api/events").json()[0]
    assert row["status"] == "changes_requested"
    assert row["error_message"] == "try force=true instead"


def test_list_events_filters_by_ticket_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    ticket = tickets.open_ticket("investigate something")
    _log("ticket_call")  # no session id set for this one below -- unassigned
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    _log("unassigned_call")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    log_event(
        server="cmp-admin", kind="tool", name="in_ticket_call", arguments={}, status="success",
        error_message=None, result_summary={}, duration_ms=1.0, ticket_id=ticket["id"],
    )

    by_ticket = client.get("/api/events", params={"ticket_id": ticket["id"]}).json()
    assert [e["name"] for e in by_ticket] == ["in_ticket_call"]

    by_unassigned = client.get("/api/events", params={"ticket_id": "unassigned"}).json()
    assert {e["name"] for e in by_unassigned} == {"ticket_call", "unassigned_call"}


def test_approving_a_plan_event_advances_its_ticket(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-b")
    ticket = tickets.open_ticket("investigate something else")
    event_id = log_pending_event(
        server="cmp-admin", kind="tool", name="submit_investigation_plan", arguments={}, ticket_id=ticket["id"],
    )

    response = client.post(f"/api/events/{event_id}/approve")

    assert response.status_code == 200
    updated = client.get(f"/api/tickets/{ticket['id']}").json()
    assert updated["state"] == "planned"


def test_denying_a_plan_event_flat_escalates_its_ticket(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-c")
    ticket = tickets.open_ticket("investigate a third thing")
    event_id = log_pending_event(
        server="cmp-admin", kind="tool", name="submit_investigation_plan", arguments={}, ticket_id=ticket["id"],
    )

    client.post(f"/api/events/{event_id}/deny")

    updated = client.get(f"/api/tickets/{ticket['id']}").json()
    assert updated["state"] == "escalated"


def test_request_changes_on_unknown_event_is_404(client: TestClient) -> None:
    response = client.post("/api/events/999999/request-changes", json={"comment": "nope"})
    assert response.status_code == 404


def test_request_changes_on_an_already_decided_event_is_409(client: TestClient) -> None:
    event_id = log_pending_event(server="cmp-admin", kind="tool", name="submit_investigation_plan", arguments={})
    client.post(f"/api/events/{event_id}/approve")
    response = client.post(f"/api/events/{event_id}/request-changes", json={"comment": "too late"})
    assert response.status_code == 409


def test_new_event_has_no_comments(client: TestClient) -> None:
    _log("some_call")
    event_id = client.get("/api/events").json()[0]["id"]
    assert client.get(f"/api/events/{event_id}/comments").json() == []
    assert client.get("/api/events").json()[0]["comment_count"] == 0


def test_add_comment_and_it_shows_up_in_the_list_and_count(client: TestClient) -> None:
    _log("some_call")
    event_id = client.get("/api/events").json()[0]["id"]

    response = client.post(f"/api/events/{event_id}/comments", json={"text": "checked this manually, looks fine"})
    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == event_id
    assert body["text"] == "checked this manually, looks fine"

    comments = client.get(f"/api/events/{event_id}/comments").json()
    assert [c["text"] for c in comments] == ["checked this manually, looks fine"]
    assert client.get("/api/events").json()[0]["comment_count"] == 1


def test_add_comment_on_unknown_event_is_404(client: TestClient) -> None:
    response = client.post("/api/events/999999/comments", json={"text": "x"})
    assert response.status_code == 404


def test_list_comments_on_unknown_event_is_404(client: TestClient) -> None:
    response = client.get("/api/events/999999/comments")
    assert response.status_code == 404


def test_add_empty_comment_is_rejected(client: TestClient) -> None:
    _log("some_call")
    event_id = client.get("/api/events").json()[0]["id"]
    response = client.post(f"/api/events/{event_id}/comments", json={"text": "   "})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_event_stream_reemits_a_still_pending_row_every_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_router, "_POLL_INTERVAL_SECONDS", 0.01)
    log_pending_event(server="cmp-admin", kind="tool", name="rebuild_server", arguments={})

    stream = events_router._event_stream()
    first = await asyncio.wait_for(stream.__anext__(), timeout=5)
    second = await asyncio.wait_for(stream.__anext__(), timeout=5)

    assert "rebuild_server" in first["data"]
    assert "rebuild_server" in second["data"]
    assert '"status":"pending"' in first["data"].replace(" ", "")


@pytest.mark.asyncio
async def test_event_stream_generator_pushes_a_newly_logged_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_router, "_POLL_INTERVAL_SECONDS", 0.01)
    stream = events_router._event_stream()

    task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0.05)  # let the generator compute last_seen and start polling
    _log("streamed_call")

    message = await asyncio.wait_for(task, timeout=5)
    assert "streamed_call" in message["data"]


@pytest.mark.asyncio
async def test_event_stream_generator_does_not_push_events_from_before_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_router, "_POLL_INTERVAL_SECONDS", 0.01)
    _log("already_existed")

    stream = events_router._event_stream()
    task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0.05)
    _log("new_after_start")

    message = await asyncio.wait_for(task, timeout=5)
    assert "new_after_start" in message["data"]
    assert "already_existed" not in message["data"]
