"""Tickets: one row per investigation, keyed by the Claude Code session id
that ran it (see `mcp_servers.shared.tickets`). This router only ever reads
the `tickets` table and (for delete) detaches events from it -- every write
that actually drives a ticket's state happens either in an MCP server
process (`start_investigate`, `instrument_dispatch`) or in `events.py`'s
approve/deny/request-changes endpoints, the moment an operator decides.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_servers.shared import db

router = APIRouter(prefix="/tickets", tags=["tickets"])

_COMMENT_EVENT_NAMES: tuple[str, ...] = ("submit_investigation_plan", "submit_investigation_report")


class TicketOut(BaseModel):
    id: int
    session_id: str
    title: str
    resource_id: str | None
    state: str
    created_at: str
    closed_at: str | None
    event_count: int
    comment_count: int
    latest_event_name: str | None
    latest_event_status: str | None
    latest_event_ts: str | None


def _row_to_ticket(row: sqlite3.Row) -> TicketOut:
    return TicketOut(
        id=row["id"],
        session_id=row["session_id"],
        title=row["title"],
        resource_id=row["resource_id"],
        state=row["state"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        event_count=row["event_count"],
        comment_count=row["comment_count"],
        latest_event_name=row["latest_event_name"],
        latest_event_status=row["latest_event_status"],
        latest_event_ts=row["latest_event_ts"],
    )


_COMMENT_NAME_PLACEHOLDERS = ", ".join("?" for _ in _COMMENT_EVENT_NAMES)

_LIST_SQL = f"""
SELECT
    tickets.*,
    (SELECT COUNT(*) FROM events WHERE events.ticket_id = tickets.id) AS event_count,
    (SELECT COUNT(*) FROM events
       WHERE events.ticket_id = tickets.id
         AND events.status = 'changes_requested'
         AND events.name IN ({_COMMENT_NAME_PLACEHOLDERS})) AS comment_count,
    (SELECT name FROM events WHERE events.ticket_id = tickets.id ORDER BY id DESC LIMIT 1) AS latest_event_name,
    (SELECT status FROM events WHERE events.ticket_id = tickets.id ORDER BY id DESC LIMIT 1) AS latest_event_status,
    (SELECT ts FROM events WHERE events.ticket_id = tickets.id ORDER BY id DESC LIMIT 1) AS latest_event_ts
FROM tickets
"""


@router.get("", response_model=list[TicketOut])
async def list_tickets() -> list[TicketOut]:
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(f"{_LIST_SQL} ORDER BY id DESC", _COMMENT_EVENT_NAMES).fetchall()
    finally:
        conn.close()
    return [_row_to_ticket(row) for row in rows]


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(ticket_id: int) -> TicketOut:
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute(
            f"{_LIST_SQL} WHERE tickets.id = ?", (*_COMMENT_EVENT_NAMES, ticket_id)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown ticket: {ticket_id}")
    return _row_to_ticket(row)


@router.delete("/{ticket_id}")
async def delete_ticket(ticket_id: int) -> dict[str, int]:
    """Detaches this ticket's events (sets their `ticket_id` back to `NULL`,
    folding them into "Unassigned") rather than deleting the underlying
    `events` rows -- consistent with this project's stance elsewhere of never
    discarding logged evidence (`clear_events` is the one deliberate bulk
    escape hatch, and it's separate/explicit). Returns how many events were
    detached, mirroring `clear_events`'s `{"deleted": N}` convention."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown ticket: {ticket_id}")
        detached = conn.execute(
            "UPDATE events SET ticket_id = NULL WHERE ticket_id = ?", (ticket_id,)
        ).rowcount
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.commit()
    finally:
        conn.close()
    return {"detached_events": detached}


__all__ = ["router"]
