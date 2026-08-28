"""The call/resource/prompt event log: a filterable listing plus a live SSE
tail. Reads the same `events` table `mcp_servers.shared.telemetry.log_event`
writes to from all three MCP servers -- this router never writes to it.

Live tail is SSE (`sse-starlette`), not a WebSocket: this only ever needs to
push server -> client, and the browser's native `EventSource` reconnects on
its own. The backend just polls `events` for new rows every
`_POLL_INTERVAL_SECONDS` -- there's no real push mechanism underneath, and
none is needed at this project's single-operator, low-volume scale.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from mcp_servers.shared import db, tickets

router = APIRouter(prefix="/events", tags=["events"])

_POLL_INTERVAL_SECONDS = 1.5


class EventOut(BaseModel):
    id: int
    ts: str
    server: str
    kind: str
    name: str
    arguments_json: str
    status: str
    error_message: str | None
    result_summary: str
    duration_ms: float
    pid: int
    action: str | None
    ticket_id: int | None
    comment_count: int


def _row_to_event(row: sqlite3.Row) -> EventOut:
    return EventOut(
        id=row["id"],
        ts=row["ts"],
        server=row["server"],
        kind=row["kind"],
        name=row["name"],
        arguments_json=row["arguments_json"],
        status=row["status"],
        error_message=row["error_message"],
        result_summary=row["result_summary"],
        duration_ms=row["duration_ms"],
        pid=row["pid"],
        action=row["action"],
        ticket_id=row["ticket_id"],
        comment_count=row["comment_count"],
    )


# `comments` are a general-purpose, per-event annotation an operator can
# leave on any logged call -- unrelated to the request-changes comment
# stored in `error_message` for a gated plan/report decision. Every row read
# here goes through this one SELECT so `comment_count` is always present,
# never a separate N+1 query per row.
_EVENTS_SELECT = (
    "SELECT events.*, "
    "(SELECT COUNT(*) FROM comments WHERE comments.event_id = events.id) AS comment_count "
    "FROM events"
)


class CommentOut(BaseModel):
    id: int
    event_id: int
    text: str
    created_at: str


class CommentCreate(BaseModel):
    text: str


def _row_to_comment(row: sqlite3.Row) -> CommentOut:
    return CommentOut(id=row["id"], event_id=row["event_id"], text=row["text"], created_at=row["created_at"])


def _normalize_ts_bound(value: str, *, field: str) -> str:
    """Parse a client-supplied ISO timestamp (any offset, or `Z`) and
    re-format it exactly like `db.utc_now_iso()` does, so the lexicographic
    comparison against stored `ts` values is actually comparing the same
    string shape rather than accidentally comparing 'Z' against '+00:00'."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


@router.get("", response_model=list[EventOut])
async def list_events(
    server: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    ticket_id: str | None = Query(
        default=None, description="Only events for this ticket id, or the literal 'unassigned' for ticket_id IS NULL"
    ),
    since: str | None = Query(default=None, description="Only events at/after this ISO timestamp"),
    until: str | None = Query(default=None, description="Only events at/before this ISO timestamp"),
    since_id: int | None = Query(default=None, description="Only events with id greater than this"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EventOut]:
    db.ensure_schema()
    conn = db.connect()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("server", server), ("kind", kind), ("status", status)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if ticket_id is not None:
            if ticket_id == "unassigned":
                clauses.append("ticket_id IS NULL")
            else:
                try:
                    parsed_ticket_id = int(ticket_id)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"invalid ticket_id: {ticket_id!r}") from exc
                clauses.append("ticket_id = ?")
                params.append(parsed_ticket_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(_normalize_ts_bound(since, field="since"))
        if until is not None:
            clauses.append("ts <= ?")
            params.append(_normalize_ts_bound(until, field="until"))
        if since_id is not None:
            clauses.append("id > ?")
            params.append(since_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"{_EVENTS_SELECT} {where} ORDER BY events.id DESC LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_event(row) for row in rows]


def _max_event_id() -> int:
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT MAX(id) AS max_id FROM events").fetchone()
    finally:
        conn.close()
    max_id = row["max_id"]
    return int(max_id) if max_id is not None else 0


async def _event_stream() -> AsyncIterator[dict[str, str]]:
    """New rows past `last_seen` stream once, same as always. A row still
    sitting at status='pending' is re-included every tick regardless of id,
    so a client connected before a decision lands still sees the status flip
    to approved/denied/success/error -- the only place an `events` row is
    ever updated in place (see `telemetry.resolve_event`)."""
    last_seen = _max_event_id()
    while True:
        conn = db.connect()
        try:
            rows = conn.execute(
                f"{_EVENTS_SELECT} WHERE events.id > ? OR events.status = 'pending' ORDER BY events.id ASC",
                (last_seen,),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            last_seen = max(last_seen, row["id"])
            yield {"event": "log", "data": _row_to_event(row).model_dump_json()}
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@router.get("/stream")
async def stream_events() -> EventSourceResponse:
    return EventSourceResponse(_event_stream())


def _decide_event(event_id: int, new_status: str) -> dict[str, str]:
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT status, name, ticket_id FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"event {event_id} is no longer pending (status={row['status']})",
            )
        conn.execute("UPDATE events SET status = ? WHERE id = ?", (new_status, event_id))
        conn.commit()
    finally:
        conn.close()
    tickets.on_decision(row["ticket_id"], row["name"], new_status)
    return {"status": new_status}


@router.delete("")
async def clear_events() -> dict[str, int]:
    """Wipe the entire event log -- a local, single-operator admin tool's
    "clear console" action, not a data-integrity-sensitive operation.
    Returns how many rows were actually removed, not just a bare 204, so
    the caller (the Logs page's "Clear logs" button) can confirm something
    really happened."""
    db.ensure_schema()
    conn = db.connect()
    try:
        deleted = conn.execute("DELETE FROM events").rowcount
        conn.commit()
    finally:
        conn.close()
    return {"deleted": deleted}


@router.post("/{event_id}/approve")
async def approve_event(event_id: int) -> dict[str, str]:
    """Unblocks the MCP dispatch call waiting on `telemetry._await_decision`
    -- it polls this same row and proceeds to run the real action once it
    sees this status."""
    return _decide_event(event_id, "approved")


@router.post("/{event_id}/deny")
async def deny_event(event_id: int) -> dict[str, str]:
    """Unblocks the waiting call without ever running the real action --
    `instrument_dispatch` returns a denied_by_operator error to the agent."""
    return _decide_event(event_id, "denied")


class RequestChangesBody(BaseModel):
    comment: str


@router.post("/{event_id}/request-changes")
async def request_changes_event(event_id: int, body: RequestChangesBody) -> dict[str, str]:
    """A third decision alongside approve/deny, for refining a submitted
    plan without a separate chat message: unblocks the waiting call the same
    way, but `instrument_dispatch` returns `{"error": "changes_requested",
    "message": body.comment}` instead of running the real action or denying
    it outright -- the agent reads the comment straight out of the tool
    result and can revise and resubmit. Only `submit_investigation_plan`'s
    own handler is written to expect this; on any other gated tool it just
    behaves like a denial with an explanation attached, since nothing else
    reads the comment back out.

    Stores the comment in `error_message` -- already the column for "why
    this call ended up in its terminal state," and unused by a pending row,
    so no schema change is needed for a second explanation-shaped field."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT status, name, ticket_id FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"event {event_id} is no longer pending (status={row['status']})",
            )
        conn.execute(
            "UPDATE events SET status = 'changes_requested', error_message = ? WHERE id = ?",
            (body.comment, event_id),
        )
        conn.commit()
    finally:
        conn.close()
    tickets.on_decision(row["ticket_id"], row["name"], "changes_requested")
    return {"status": "changes_requested"}


@router.get("/{event_id}/comments", response_model=list[CommentOut])
async def list_comments(event_id: int) -> list[CommentOut]:
    db.ensure_schema()
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
        rows = conn.execute(
            "SELECT * FROM comments WHERE event_id = ? ORDER BY id ASC", (event_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_comment(row) for row in rows]


@router.post("/{event_id}/comments", response_model=CommentOut)
async def add_comment(event_id: int, body: CommentCreate) -> CommentOut:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="comment text must not be empty")
    db.ensure_schema()
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
        cursor = conn.execute(
            "INSERT INTO comments (event_id, text, created_at) VALUES (?, ?, ?)",
            (event_id, text, db.utc_now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (cursor.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_comment(row)


__all__ = ["router"]
