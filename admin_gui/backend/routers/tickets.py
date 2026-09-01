"""Tickets: one row per investigation, keyed by the Claude Code session id
that ran it (see `mcp_servers.shared.tickets`). This router reads the
`tickets` table, writes the admin-GUI-only concepts layered on top of it
(rename, soft-delete/restore via `deleted_at`, hard delete) -- every write
that actually drives a ticket's *state* still happens either in an MCP
server process (`start_investigate`, `instrument_dispatch`) or in
`events.py`'s approve/deny/request-changes endpoints, the moment an operator
decides.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mcp_servers.shared import db

router = APIRouter(prefix="/tickets", tags=["tickets"])

_COMMENT_EVENT_NAMES: tuple[str, ...] = ("submit_investigation_plan", "submit_investigation_report")


class TicketOut(BaseModel):
    id: int
    session_id: str
    title: str
    initial_prompt: str | None
    resource_id: str | None
    state: str
    created_at: str
    closed_at: str | None
    deleted_at: str | None
    event_count: int
    comment_count: int
    latest_event_name: str | None
    latest_event_status: str | None
    latest_event_ts: str | None
    latest_event_action: str | None


class TicketUpdate(BaseModel):
    title: str | None = None


class TicketTransitionOut(BaseModel):
    id: int
    ticket_id: int
    ticket_title: str
    from_state: str | None
    to_state: str
    ts: str


def _row_to_ticket(row: sqlite3.Row) -> TicketOut:
    return TicketOut(
        id=row["id"],
        session_id=row["session_id"],
        title=row["title"],
        initial_prompt=row["initial_prompt"],
        resource_id=row["resource_id"],
        state=row["state"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        deleted_at=row["deleted_at"],
        event_count=row["event_count"],
        comment_count=row["comment_count"],
        latest_event_name=row["latest_event_name"],
        latest_event_status=row["latest_event_status"],
        latest_event_ts=row["latest_event_ts"],
        latest_event_action=row["latest_event_action"],
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
    (SELECT ts FROM events WHERE events.ticket_id = tickets.id ORDER BY id DESC LIMIT 1) AS latest_event_ts,
    (SELECT action FROM events WHERE events.ticket_id = tickets.id ORDER BY id DESC LIMIT 1) AS latest_event_action
FROM tickets
"""


@router.get("", response_model=list[TicketOut])
async def list_tickets(trashed: bool = False) -> list[TicketOut]:
    """Excludes trashed tickets by default -- every existing caller
    (Dashboard's Kanban board, the Logs page's ticket filter dropdown) calls
    this with no args and so is unaffected by trash entirely. Pass
    `trashed=true` for the Tickets page's own Trash section."""
    db.ensure_schema()
    conn = db.connect()
    try:
        clause = "WHERE tickets.deleted_at IS NOT NULL" if trashed else "WHERE tickets.deleted_at IS NULL"
        rows = conn.execute(f"{_LIST_SQL} {clause} ORDER BY id DESC", _COMMENT_EVENT_NAMES).fetchall()
    finally:
        conn.close()
    return [_row_to_ticket(row) for row in rows]


@router.get("/transitions", response_model=list[TicketTransitionOut])
async def list_ticket_transitions(limit: int = Query(default=500, ge=1, le=5000)) -> list[TicketTransitionOut]:
    """State-change history for the Tickets page's timeline -- one row per
    transition (see `mcp_servers.shared.tickets._record_transition`).
    Declared before `/{ticket_id}` below: FastAPI matches routes in
    declaration order, so a later declaration here would have this literal
    `transitions` path swallowed by `/{ticket_id}`'s `int` coercion (a 422,
    never reaching this handler). Excludes trashed tickets, same as the
    default ticket list."""
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT ticket_state_transitions.id, ticket_state_transitions.ticket_id, "
            "tickets.title AS ticket_title, ticket_state_transitions.from_state, "
            "ticket_state_transitions.to_state, ticket_state_transitions.ts "
            "FROM ticket_state_transitions "
            "JOIN tickets ON tickets.id = ticket_state_transitions.ticket_id "
            "WHERE tickets.deleted_at IS NULL "
            "ORDER BY ticket_state_transitions.ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [TicketTransitionOut(**dict(row)) for row in rows]


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


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(ticket_id: int, update: TicketUpdate) -> TicketOut:
    """Renames a ticket -- the only field an operator can edit. Never
    touches `initial_prompt`: that's the immutable snapshot of how the
    ticket started (see `mcp_servers.shared.tickets.open_ticket`)."""
    fields = update.model_dump(exclude_unset=True)
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be blank")
        fields["title"] = title
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown ticket: {ticket_id}")
        if fields:
            conn.execute("UPDATE tickets SET title = ? WHERE id = ?", (fields["title"], ticket_id))
            conn.commit()
        updated = conn.execute(
            f"{_LIST_SQL} WHERE tickets.id = ?", (*_COMMENT_EVENT_NAMES, ticket_id)
        ).fetchone()
    finally:
        conn.close()
    assert updated is not None
    return _row_to_ticket(updated)


@router.post("/{ticket_id}/trash", response_model=TicketOut)
async def trash_ticket(ticket_id: int) -> TicketOut:
    """Soft-delete: sets `deleted_at` so the ticket drops out of the default
    list but stays fully intact -- reversible via `restore_ticket` below.
    Idempotent: trashing an already-trashed ticket just re-stamps the
    timestamp, harmless."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown ticket: {ticket_id}")
        conn.execute("UPDATE tickets SET deleted_at = ? WHERE id = ?", (db.utc_now_iso(), ticket_id))
        conn.commit()
        updated = conn.execute(
            f"{_LIST_SQL} WHERE tickets.id = ?", (*_COMMENT_EVENT_NAMES, ticket_id)
        ).fetchone()
    finally:
        conn.close()
    assert updated is not None
    return _row_to_ticket(updated)


@router.post("/{ticket_id}/restore", response_model=TicketOut)
async def restore_ticket(ticket_id: int) -> TicketOut:
    """Clears `deleted_at`, undoing `trash_ticket` above."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown ticket: {ticket_id}")
        conn.execute("UPDATE tickets SET deleted_at = NULL WHERE id = ?", (ticket_id,))
        conn.commit()
        updated = conn.execute(
            f"{_LIST_SQL} WHERE tickets.id = ?", (*_COMMENT_EVENT_NAMES, ticket_id)
        ).fetchone()
    finally:
        conn.close()
    assert updated is not None
    return _row_to_ticket(updated)


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
