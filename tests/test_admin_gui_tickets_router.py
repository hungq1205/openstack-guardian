"""Tests for `/api/tickets`: list/get/delete against the tickets table
`mcp_servers.shared.tickets` writes to. The state-machine transitions
themselves are covered in `test_ticket_lifecycle.py` and
`test_admin_gui_events_router.py`; this file is about the read/delete
surface only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_gui.backend.app import app
from mcp_servers.shared import tickets
from mcp_servers.shared.telemetry import log_event


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_tickets_is_empty_by_default(client: TestClient) -> None:
    response = client.get("/api/tickets")
    assert response.status_code == 200
    assert response.json() == []


def test_list_tickets_returns_newest_first_with_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    first = tickets.open_ticket("first")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-b")
    second = tickets.open_ticket("second")

    log_event(
        server="cmp-admin", kind="tool", name="search_logs", arguments={}, status="success",
        error_message=None, result_summary={}, duration_ms=1.0, ticket_id=second["id"],
    )

    response = client.get("/api/tickets").json()
    assert [row["id"] for row in response] == [second["id"], first["id"]]
    assert response[0]["event_count"] == 1
    assert response[1]["event_count"] == 0


def test_get_ticket_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/api/tickets/999999")
    assert response.status_code == 404


def test_get_ticket_returns_comment_count_from_changes_requested_decisions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-c")
    ticket = tickets.open_ticket("t")
    log_event(
        server="cmp-admin", kind="tool", name="submit_investigation_plan", arguments={},
        status="changes_requested", error_message="try again", result_summary={}, duration_ms=1.0,
        ticket_id=ticket["id"],
    )
    log_event(
        server="cmp-admin", kind="tool", name="search_logs", arguments={}, status="changes_requested",
        error_message="irrelevant status combo, should never count", result_summary={}, duration_ms=1.0,
        ticket_id=ticket["id"],
    )

    response = client.get(f"/api/tickets/{ticket['id']}").json()
    assert response["comment_count"] == 1
    assert response["event_count"] == 2


def test_delete_ticket_detaches_its_events_instead_of_deleting_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-d")
    ticket = tickets.open_ticket("t")
    log_event(
        server="cmp-admin", kind="tool", name="search_logs", arguments={}, status="success",
        error_message=None, result_summary={}, duration_ms=1.0, ticket_id=ticket["id"],
    )
    log_event(
        server="cmp-admin", kind="tool", name="search_failure_patterns", arguments={}, status="success",
        error_message=None, result_summary={}, duration_ms=1.0, ticket_id=ticket["id"],
    )

    response = client.delete(f"/api/tickets/{ticket['id']}")
    assert response.status_code == 200
    assert response.json() == {"detached_events": 2}

    assert client.get(f"/api/tickets/{ticket['id']}").status_code == 404
    unassigned = client.get("/api/events", params={"ticket_id": "unassigned"}).json()
    assert {row["name"] for row in unassigned} == {"search_logs", "search_failure_patterns"}


def test_delete_ticket_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.delete("/api/tickets/999999")
    assert response.status_code == 404


def test_update_ticket_renames_without_touching_initial_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-rename")
    ticket = tickets.open_ticket("original title", "the raw original prompt")

    response = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "renamed title"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "renamed title"
    assert body["initial_prompt"] == "the raw original prompt"


def test_update_ticket_rejects_a_blank_title(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-rename-blank")
    ticket = tickets.open_ticket("t")
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "   "})
    assert response.status_code == 400


def test_update_ticket_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.patch("/api/tickets/999999", json={"title": "x"})
    assert response.status_code == 404


def test_trash_and_restore_round_trip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-trash")
    ticket = tickets.open_ticket("t")

    trashed = client.post(f"/api/tickets/{ticket['id']}/trash")
    assert trashed.status_code == 200
    assert trashed.json()["deleted_at"] is not None

    # excluded from the default list, present in the trashed one
    assert ticket["id"] not in [row["id"] for row in client.get("/api/tickets").json()]
    assert ticket["id"] in [row["id"] for row in client.get("/api/tickets", params={"trashed": True}).json()]
    # still fetchable directly by id even while trashed
    assert client.get(f"/api/tickets/{ticket['id']}").status_code == 200

    restored = client.post(f"/api/tickets/{ticket['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert ticket["id"] in [row["id"] for row in client.get("/api/tickets").json()]
    assert ticket["id"] not in [row["id"] for row in client.get("/api/tickets", params={"trashed": True}).json()]


def test_trash_and_restore_return_404_for_unknown_id(client: TestClient) -> None:
    assert client.post("/api/tickets/999999/trash").status_code == 404
    assert client.post("/api/tickets/999999/restore").status_code == 404


def test_list_ticket_transitions_orders_newest_first_and_excludes_trashed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-transitions-a")
    kept = tickets.open_ticket("kept ticket")
    tickets.on_decision(kept["id"], "submit_investigation_plan", "approved")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-transitions-b")
    trashed_ticket = tickets.open_ticket("trashed ticket")
    client.post(f"/api/tickets/{trashed_ticket['id']}/trash")

    response = client.get("/api/tickets/transitions")
    assert response.status_code == 200
    body = response.json()

    assert all(row["ticket_id"] != trashed_ticket["id"] for row in body)
    kept_rows = [row for row in body if row["ticket_id"] == kept["id"]]
    assert [row["to_state"] for row in kept_rows] == ["planned", "investigating"]  # newest first
    assert kept_rows[0]["ticket_title"] == "kept ticket"


def test_list_ticket_transitions_route_takes_precedence_over_ticket_id(client: TestClient) -> None:
    # If route ordering ever regresses, "/transitions" gets coerced as a
    # ticket_id path param instead and this 422s.
    response = client.get("/api/tickets/transitions")
    assert response.status_code == 200
