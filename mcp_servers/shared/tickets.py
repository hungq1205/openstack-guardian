"""Ticket = one investigation, identified by the Claude Code session that ran
it (`CLAUDE_CODE_SESSION_ID`), not by an argument any tool call has to carry.

This is deliberately the only place session/ticket logic lives, so it stays
an outer layer any MCP server implementation can plug into without changing
its own tool/resource schemas: `mcp_servers.shared.telemetry.instrument_dispatch`
calls `current_ticket_id()` once per dispatch to attribute the event, and
`on_pending`/`on_dispatched` right alongside it; the admin GUI's approve/deny/
request-changes endpoints call `on_decision` at the moment an operator
actually makes a decision. Nothing about a tool's own inputSchema changes --
`start_investigate` is the only tool that touches this module directly (to
open a ticket), and `submit_investigation_plan`'s handler calls
`backfill_resource_id` once resource_id becomes known.

State machine: investigating -> planned -> resolving -> in_review -> completed,
with escalated reachable from anywhere (a flat plan/report denial, or
notify_admin actually running). See the project's ticket-tracking design doc
for the full transition table -- summarized in each function's docstring
below.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from mcp_servers.shared import db

logger = logging.getLogger(__name__)

_SESSION_ENV_VAR = "CLAUDE_CODE_SESSION_ID"

_LIFECYCLE_TOOLS = {"submit_investigation_plan", "submit_investigation_report", "notify_admin"}


def current_session_id() -> str | None:
    return os.environ.get(_SESSION_ENV_VAR) or None


def current_ticket_id(conn: sqlite3.Connection | None = None) -> int | None:
    """Looked up fresh on every call -- cheap indexed SELECT, always reflects
    the latest state (e.g. a ticket closing mid-session stops attributing
    further calls to it until a new one opens). Returns `None` if this
    process has no session id, no ticket has been opened for it (yet, or
    ever), or the store itself can't be reached -- called from every single
    dispatch (`instrument_dispatch`), so a broken log sink must degrade this
    to "unattributed" rather than breaking the real tool call, exactly like
    `telemetry.log_event`'s own best-effort convention."""
    session_id = current_session_id()
    if session_id is None:
        return None
    try:
        db.ensure_schema()
        owns_conn = conn is None
        active_conn = conn or db.connect()
        try:
            row = active_conn.execute(
                "SELECT id FROM tickets WHERE session_id = ?", (session_id,)
            ).fetchone()
            return row["id"] if row is not None else None
        finally:
            if owns_conn:
                active_conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to look up current ticket", exc_info=True)
        return None


def _record_transition(
    conn: sqlite3.Connection, ticket_id: int, *, from_state: str | None, to_state: str, ts: str | None = None
) -> None:
    """Appends one row to `ticket_state_transitions` -- the append-only log
    the admin GUI's timeline reads (`tickets.state` itself only ever holds
    the *current* state). Takes the caller's connection so it commits
    atomically with whatever state change it's recording, never a separate
    best-effort write of its own."""
    conn.execute(
        "INSERT INTO ticket_state_transitions (ticket_id, from_state, to_state, ts) VALUES (?, ?, ?, ?)",
        (ticket_id, from_state, to_state, ts or db.utc_now_iso()),
    )


def open_ticket(title: str, initial_prompt: str | None = None) -> dict[str, Any]:
    """Idempotent per session -- `start_investigate` called twice in one
    session returns the existing ticket rather than erroring or duplicating,
    since one session is always exactly one investigation.

    `initial_prompt` is the user's original request, snapshotted once and
    never edited again -- `title` alone used to serve both roles, but is now
    a renamable display name, so callers that only ever had one string
    (existing tests, any future single-string caller) get `initial_prompt`
    defaulted to `title` rather than losing it."""
    session_id = current_session_id()
    if session_id is None:
        return {
            "error": "no_session",
            "message": f"{_SESSION_ENV_VAR} is not set in this process's environment",
        }
    prompt = initial_prompt if initial_prompt is not None else title
    db.ensure_schema()
    conn = db.connect()
    try:
        existing = conn.execute("SELECT * FROM tickets WHERE session_id = ?", (session_id,)).fetchone()
        if existing is not None:
            return dict(existing)
        created_at = db.utc_now_iso()
        cursor = conn.execute(
            "INSERT INTO tickets (session_id, title, initial_prompt, state, created_at) "
            "VALUES (?, ?, ?, 'investigating', ?)",
            (session_id, title, prompt, created_at),
        )
        ticket_id = cursor.lastrowid
        assert ticket_id is not None
        _record_transition(conn, ticket_id, from_state=None, to_state="investigating", ts=created_at)
        conn.commit()
        return {
            "id": ticket_id,
            "session_id": session_id,
            "title": title,
            "initial_prompt": prompt,
            "resource_id": None,
            "state": "investigating",
            "created_at": created_at,
            "closed_at": None,
        }
    finally:
        conn.close()


def backfill_resource_id(ticket_id: int | None, resource_id: str | None) -> None:
    """Fills in `tickets.resource_id` the first time it becomes known
    (typically when `submit_investigation_plan` first runs for this ticket).
    Never overwrites an already-known value. Best-effort like every other
    hook in this module below `open_ticket` -- called from a tool handler
    that has already done its real job, so a bookkeeping failure here must
    not turn into a failed plan submission."""
    if ticket_id is None or not resource_id:
        return
    try:
        db.ensure_schema()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE tickets SET resource_id = ? WHERE id = ? AND resource_id IS NULL",
                (resource_id, ticket_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to backfill ticket %s resource_id", ticket_id, exc_info=True)


def _set_state(ticket_id: int, state: str) -> None:
    try:
        db.ensure_schema()
        conn = db.connect()
        try:
            row = conn.execute("SELECT state FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            conn.execute("UPDATE tickets SET state = ? WHERE id = ?", (state, ticket_id))
            _record_transition(conn, ticket_id, from_state=row["state"] if row else None, to_state=state)
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to set ticket %s state to %s", ticket_id, state, exc_info=True)


def _close(ticket_id: int, state: str) -> None:
    try:
        db.ensure_schema()
        conn = db.connect()
        try:
            row = conn.execute("SELECT state FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            conn.execute(
                "UPDATE tickets SET state = ?, closed_at = ? WHERE id = ?",
                (state, db.utc_now_iso(), ticket_id),
            )
            _record_transition(conn, ticket_id, from_state=row["state"] if row else None, to_state=state)
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to close ticket %s as %s", ticket_id, state, exc_info=True)


def _bump_if(ticket_id: int, *, from_state: str, to_state: str) -> None:
    try:
        db.ensure_schema()
        conn = db.connect()
        try:
            cursor = conn.execute(
                "UPDATE tickets SET state = ? WHERE id = ? AND state = ?",
                (to_state, ticket_id, from_state),
            )
            if cursor.rowcount > 0:
                _record_transition(conn, ticket_id, from_state=from_state, to_state=to_state)
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to bump ticket %s from %s to %s", ticket_id, from_state, to_state, exc_info=True)


def on_pending(ticket_id: int | None, name: str) -> None:
    """Called the moment a gated call goes 'pending' -- i.e. before any
    operator decision. Only `submit_investigation_report` has a transition
    here: filing a report immediately means the ticket is now `in_review`,
    win or lose, since that's simply "waiting on a decision" -- same as a
    Plan already goes pending immediately on submission."""
    if ticket_id is not None and name == "submit_investigation_report":
        _set_state(ticket_id, "in_review")


def on_dispatched(ticket_id: int | None, name: str, requires_approval: bool) -> None:
    """Bumps `planned` -> `resolving` on the first real action dispatched
    after the plan's approval. Only fires for genuine action tools -- the
    three ticket-lifecycle tools have their own explicit transitions in
    `on_pending`/`on_decision` and must not also trigger this generic bump."""
    if ticket_id is None or not requires_approval or name in _LIFECYCLE_TOOLS:
        return
    _bump_if(ticket_id, from_state="planned", to_state="resolving")


def on_decision(ticket_id: int | None, name: str, decision: str) -> None:
    """Called the moment an operator approves/denies/requests-changes on a
    gated call. `decision` is one of 'approved' | 'denied' | 'changes_requested'.

    - plan approved -> planned; denied with comment -> investigating;
      denied flat -> escalated (a plain "no" is treated as final, same rule
      Step 6 already applies to a denied execution call).
    - report approved -> completed (closed); denied with comment ->
      resolving (more work needed, not just a rewrite); denied flat ->
      escalated (closed).
    - notify_admin approved (i.e. it actually ran) -> escalated (closed),
      regardless of what state the ticket was already in.
    """
    if ticket_id is None:
        return
    if name == "submit_investigation_plan":
        if decision == "approved":
            _set_state(ticket_id, "planned")
        elif decision == "changes_requested":
            _set_state(ticket_id, "investigating")
        elif decision == "denied":
            _close(ticket_id, "escalated")
    elif name == "submit_investigation_report":
        if decision == "approved":
            _close(ticket_id, "completed")
        elif decision == "changes_requested":
            _set_state(ticket_id, "resolving")
        elif decision == "denied":
            _close(ticket_id, "escalated")
    elif name == "notify_admin" and decision == "approved":
        _close(ticket_id, "escalated")


__all__ = [
    "backfill_resource_id",
    "current_session_id",
    "current_ticket_id",
    "on_decision",
    "on_dispatched",
    "on_pending",
    "open_ticket",
]
