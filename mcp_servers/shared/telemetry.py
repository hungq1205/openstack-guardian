"""Call/resource/prompt logging shared by every MCP server's dispatch
handlers -- see `instrument_dispatch`, wired into the six real dispatch
points across `openapi_bridge.py`, `prompts/__init__.py`, `cmp_logs_mcp/server.py`,
and `cmp_notify_mcp/server.py`.

Nothing here ever raises on its own: a broken log sink must never break a
real tool call. Exceptions raised *by* the wrapped dispatch (e.g. `get_prompt`
on an unknown name) are logged as errors and then re-raised unchanged.

`requires_approval` is the one exception to "never blocks": when set, the
call is held open behind a human decision made through the admin GUI (see
`admin_gui/backend/routers/events.py`'s approve/deny/request-changes
endpoints) before `dispatch()` ever runs. There's no timeout -- a pending
call waits until someone decides, or the process is killed, by this
project's explicit choice to keep a stuck approval visibly pending rather
than silently expiring it. A decision resolves to one of three outcomes:
approved (`dispatch()` runs), denied (`{"error": "denied_by_operator", ...}`,
`dispatch()` never runs), or changes requested (`{"error":
"changes_requested", "message": <the operator's comment>}`, `dispatch()`
never runs) -- the last one exists so an operator can ask for a revised
plan from `submit_investigation_plan` without a separate chat message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp_servers.shared import db, tickets
from mcp_servers.shared.masking import mask_value

logger = logging.getLogger(__name__)

_MAX_FIELD_CHARS = 4000
_APPROVAL_POLL_SECONDS = 1.0


def _to_plain(value: Any) -> Any:
    """Normalize pydantic models (e.g. a prompt's `types.GetPromptResult`) to
    plain dict/list/str before masking -- `mask_value` only recurses through
    `Mapping`/`Sequence`, so a raw pydantic object would otherwise sail
    through unmasked."""
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", exclude_none=True)
        except (TypeError, ValueError):
            return str(value)
    return value


def _dump(value: Any) -> str:
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:_MAX_FIELD_CHARS]


def log_event(
    *,
    server: str,
    kind: str,
    name: str,
    arguments: Any,
    status: str,
    error_message: str | None,
    result_summary: Any,
    duration_ms: float,
    action: str | None = None,
    ticket_id: int | None = None,
) -> None:
    try:
        db.ensure_schema()
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO events "
                "(ts, server, kind, name, arguments_json, status, error_message, "
                "result_summary, duration_ms, pid, action, ticket_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    db.utc_now_iso(),
                    server,
                    kind,
                    name,
                    _dump(arguments),
                    status,
                    error_message,
                    _dump(result_summary),
                    duration_ms,
                    os.getpid(),
                    action,
                    ticket_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to log event", exc_info=True)


def log_pending_event(
    *,
    server: str,
    kind: str,
    name: str,
    arguments: Any,
    action: str | None = None,
    ticket_id: int | None = None,
) -> int:
    """Insert a 'pending' row awaiting an operator's approve/deny decision,
    returning its id for `_await_decision`/`resolve_event` to track.

    Unlike `log_event`, this does NOT swallow a DB failure: if the pending
    row can't even be created, there is no channel to ask for approval at
    all, and `instrument_dispatch` needs to see that failure rather than
    silently letting a gated call through unreviewed.
    """
    db.ensure_schema()
    conn = db.connect()
    try:
        cursor = conn.execute(
            "INSERT INTO events "
            "(ts, server, kind, name, arguments_json, status, error_message, "
            "result_summary, duration_ms, pid, action, ticket_id) "
            "VALUES (?, ?, ?, ?, ?, 'pending', NULL, '', 0, ?, ?, ?)",
            (db.utc_now_iso(), server, kind, name, _dump(arguments), os.getpid(), action, ticket_id),
        )
        conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    finally:
        conn.close()


def resolve_event(
    event_id: int,
    *,
    status: str,
    error_message: str | None,
    result_summary: Any,
    duration_ms: float,
) -> None:
    """Overwrite a pending row with its terminal outcome -- the one place an
    `events` row is ever updated after insert, since a gated call's real
    result (denied, or approved-and-ran) isn't known until later. Best-effort
    like `log_event`: by the time this runs the real-world outcome (denied,
    or the actual dispatch) has already happened, so a failure here is a
    bookkeeping gap, not a safety one."""
    try:
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE events SET status = ?, error_message = ?, result_summary = ?, duration_ms = ? WHERE id = ?",
                (status, error_message, _dump(result_summary), duration_ms, event_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.debug("failed to resolve event %s", event_id, exc_info=True)


async def _await_decision(event_id: int) -> str:
    """Poll until `event_id`'s status moves off 'pending' -- set by an
    operator clicking Approve/Deny in the admin GUI. No timeout by design;
    see the module docstring."""
    while True:
        conn = db.connect()
        try:
            row = conn.execute("SELECT status FROM events WHERE id = ?", (event_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return "denied"
        if row["status"] != "pending":
            return str(row["status"])
        await asyncio.sleep(_APPROVAL_POLL_SECONDS)


def _error_message_from(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    message = result.get("message")
    return str(message) if message is not None else str(result.get("error"))


def _read_comment(event_id: int) -> str | None:
    """The `error_message` the request-changes endpoint already wrote onto
    this row the moment the operator submitted it -- fetched fresh rather
    than threaded through in memory, since the endpoint and this waiting
    call are two separate processes sharing only the sqlite file."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT error_message FROM events WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    return row["error_message"] if row is not None else None


async def instrument_dispatch[T](
    *,
    server: str,
    kind: str,
    name: str,
    arguments: Any,
    source: str,
    dispatch: Callable[[], Awaitable[T]],
    action: str | None = None,
    requires_approval: bool = False,
) -> T:
    """Run and time `dispatch()`, logging exactly one `events` row for it.

    Success/error is classified by this project's existing `{"error": ...}`
    envelope convention when `dispatch()` returns a dict; anything else
    (e.g. a `types.GetPromptResult`) that returns without raising counts as
    success. Arguments and results are masked (`shared.masking.mask_value`)
    before ever being persisted.

    `action` is the human-readable "what actually ran" -- an HTTP method +
    path for an API-backed call today, a shell command once this project
    grows one, `None` when a call is purely local (a meta-tool, a
    knowledge-base lookup) and there is no external action to name.

    `requires_approval` gates `dispatch()` behind an operator decision (see
    the module docstring): a 'pending' row is logged first, and this call
    blocks until it's approved or denied. A denial never runs `dispatch()`
    at all and returns a `{"error": "denied_by_operator", ...}` result
    instead -- callers that gate this must return `dict[str, Any]`.
    """
    masked_arguments = mask_value(_to_plain(arguments), source=source)
    ticket_id = tickets.current_ticket_id()

    event_id: int | None = None
    if requires_approval:
        wait_start = time.monotonic()
        event_id = log_pending_event(
            server=server, kind=kind, name=name, arguments=masked_arguments, action=action, ticket_id=ticket_id
        )
        tickets.on_pending(ticket_id, name)
        decision = await _await_decision(event_id)
        if decision == "changes_requested":
            wait_ms = (time.monotonic() - wait_start) * 1000
            comment = _read_comment(event_id)
            resolve_event(
                event_id, status="changes_requested", error_message=comment, result_summary=None, duration_ms=wait_ms
            )
            return {"error": "changes_requested", "message": comment}  # type: ignore[return-value]
        if decision != "approved":
            wait_ms = (time.monotonic() - wait_start) * 1000
            message = f"{name} was denied by the operator in the admin console."
            resolve_event(event_id, status="denied", error_message=message, result_summary=None, duration_ms=wait_ms)
            return {"error": "denied_by_operator", "message": message}  # type: ignore[return-value]

    tickets.on_dispatched(ticket_id, name, requires_approval)

    start = time.monotonic()
    try:
        result = await dispatch()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        if event_id is not None:
            resolve_event(
                event_id, status="error", error_message=str(exc), result_summary=None, duration_ms=duration_ms
            )
        else:
            log_event(
                server=server,
                kind=kind,
                name=name,
                arguments=masked_arguments,
                status="error",
                error_message=str(exc),
                result_summary=None,
                duration_ms=duration_ms,
                action=action,
                ticket_id=ticket_id if ticket_id is not None else tickets.current_ticket_id(),
            )
        raise
    duration_ms = (time.monotonic() - start) * 1000
    status = "error" if isinstance(result, dict) and "error" in result else "success"
    error_message = _error_message_from(result) if status == "error" else None
    masked_result = mask_value(_to_plain(result), source=source)
    if ticket_id is None:
        # `dispatch()` may have just opened the ticket this very call belongs
        # to (e.g. `start_investigate` itself) -- re-check once, after the
        # fact, rather than always missing that one self-referential event.
        ticket_id = tickets.current_ticket_id()
    if event_id is not None:
        resolve_event(
            event_id, status=status, error_message=error_message, result_summary=masked_result, duration_ms=duration_ms
        )
    else:
        log_event(
            server=server,
            kind=kind,
            name=name,
            arguments=masked_arguments,
            status=status,
            error_message=error_message,
            result_summary=masked_result,
            duration_ms=duration_ms,
            action=action,
            ticket_id=ticket_id,
        )
    return result


__all__ = ["instrument_dispatch", "log_event", "log_pending_event", "resolve_event"]
