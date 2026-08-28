"""Three hand-built tools that give the investigate-incident agent skill a
real MCP-level record instead of chat prose, for the moments that skill used
to only ever write as a plain chat message:

- `start_investigate` -- opens a ticket for this investigation, keyed by
  this process's Claude Code session id (see `mcp_servers.shared.tickets`).
  Not gated -- it's bookkeeping, not a state change to real infrastructure.
  This is also the *only* one of the three that touches `tickets.py`
  directly; every other tool call and resource read in this investigation is
  attributed to the resulting ticket automatically, with no argument to
  carry -- see `mcp_servers.shared.telemetry.instrument_dispatch`.
- `submit_investigation_plan` -- the pre-approval plan (evidence, hypothesis,
  reasoning, proposed action, expected result, limitations). Gated behind
  the same operator-approval mechanism as any state-changing action tool
  (see `mcp_servers.shared.telemetry.instrument_dispatch`): submitting it
  logs a pending row the admin GUI already renders and can approve/deny,
  with full detail, with no changes needed there -- that machinery is
  generic over any gated tool call, not specific to CMP actions.
- `submit_investigation_report` -- the closing report. Logged the same way
  and gated the same way as the plan: this used to be an ungated record of
  what already happened, but the report itself is now also subject to
  operator review (approve/deny/request-changes), since an operator may
  disagree with the write-up even after the underlying fix already ran.

All three are purely local like `search_failure_patterns` -- no CMP/core API
call, and no state anywhere but this project's own audit log plus the small
`tickets` table `start_investigate` writes to. Living here rather than as
their own MCP server (contrast `cmp_notify_mcp`, which is a tiny hand-rolled
server for a single tool) because there's no protocol reason to split them
off: they're `cmp-admin` `ExtraTool`s exactly like `search_failure_patterns`,
and `cmp-admin` already owns every other approval-gated call this skill
makes.
"""

from __future__ import annotations

from typing import Any

from mcp import types

from mcp_servers.openapi_bridge import ExtraTool
from mcp_servers.shared import tickets

_START_INVESTIGATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "The user's original request/prompt, verbatim or lightly summarized -- "
                "becomes this ticket's display title in the admin GUI."
            ),
        },
    },
    "required": ["title"],
}

_SUBMIT_PLAN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resource_id": {
            "type": "string",
            "description": "The id of the resource this incident concerns (server, volume, floating IP, etc).",
        },
        "pattern_id": {
            "type": "string",
            "description": "The matched failure-pattern id, if search_failure_patterns found one.",
        },
        "evidence": {
            "type": "string",
            "description": "The log lines / resource state this plan is based on.",
        },
        "hypothesis": {
            "type": "string",
            "description": "The cause you believe this is.",
        },
        "reasoning": {
            "type": "string",
            "description": "Why the proposed action follows from the evidence and hypothesis.",
        },
        "proposed_action": {
            "type": "string",
            "description": "The exact instruction/tool calls you're about to make.",
        },
        "expected_result": {
            "type": "string",
            "description": "What should happen if this fixes it.",
        },
        "limitations": {
            "type": "string",
            "description": (
                "What you couldn't confirm, an assumption the hypothesis rests on, a case "
                "this plan doesn't cover, or anything else you're not sure about. State "
                "plainly if the evidence fully supports the plan instead of leaving this "
                "empty -- don't pad it with hedging that doesn't matter either."
            ),
        },
    },
    "required": [
        "resource_id",
        "evidence",
        "hypothesis",
        "reasoning",
        "proposed_action",
        "expected_result",
        "limitations",
    ],
}

_SUBMIT_REPORT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resource_id": {
            "type": "string",
            "description": "The id of the resource this incident concerned.",
        },
        "observed_failure": {
            "type": "string",
            "description": "The failure as originally observed/reported.",
        },
        "evidence": {
            "type": "string",
            "description": "The evidence gathered over the course of the investigation.",
        },
        "root_cause_hypothesis": {
            "type": "string",
            "description": "The root cause you settled on, if you settled on one.",
        },
        "confidence": {
            "type": "string",
            "description": "Your confidence in the root-cause hypothesis and/or the outcome.",
        },
        "action_taken": {
            "type": "string",
            "description": "Any action you took, if any.",
        },
        "result": {
            "type": "string",
            "description": "The outcome -- resolved, not resolved, denied, escalated, etc.",
        },
        "remaining_uncertainty": {
            "type": "string",
            "description": (
                "Anything still uncertain. State plainly if nothing is instead of leaving "
                "this empty."
            ),
        },
    },
    "required": [
        "resource_id",
        "observed_failure",
        "evidence",
        "confidence",
        "result",
        "remaining_uncertainty",
    ],
}


def run_start_investigate(arguments: dict[str, Any]) -> dict[str, Any]:
    title = str(arguments.get("title") or "").strip()
    if not title:
        return {"error": "missing_title", "message": "start_investigate requires a non-empty title"}
    return tickets.open_ticket(title)


def run_submit_investigation_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    """Only ever runs after the operator has approved the pending row
    `instrument_dispatch` logs for this call -- the plan itself was already
    the durable record the moment it was submitted; this return value is
    just the agent's signal that it's clear to move on to execution.

    Backfills the ticket's `resource_id` the first time it's known for this
    investigation -- `start_investigate` only ever takes a `title`, since the
    concrete resource is often only discovered during evidence-gathering."""
    tickets.backfill_resource_id(tickets.current_ticket_id(), arguments.get("resource_id"))
    return {
        "status": "plan_approved",
        "resource_id": arguments.get("resource_id"),
        "pattern_id": arguments.get("pattern_id"),
    }


def run_submit_investigation_report(arguments: dict[str, Any]) -> dict[str, Any]:
    """Only ever runs after the operator has approved the pending row
    `instrument_dispatch` logs for this call -- same gating as the plan."""
    return {"status": "logged", "resource_id": arguments.get("resource_id")}


def build_start_investigate_tool() -> ExtraTool:
    tool = types.Tool(
        name="start_investigate",
        description=(
            "Open a ticket for this investigation, using the user's original request as its "
            "title. Call this first, before anything else -- every subsequent tool call and "
            "resource read in this conversation is then attributed to this ticket "
            "automatically, with no argument to carry. Not gated: this is bookkeeping, not a "
            "state change to real infrastructure. Calling it again in the same conversation "
            "returns the same ticket rather than creating a second one."
        ),
        inputSchema=_START_INVESTIGATE_INPUT_SCHEMA,
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
    )
    return ExtraTool(tool=tool, handler=run_start_investigate)


def build_submit_investigation_plan_tool() -> ExtraTool:
    tool = types.Tool(
        name="submit_investigation_plan",
        description=(
            "Submit the pre-approval investigation plan (evidence, hypothesis, reasoning, "
            "proposed action, expected result, limitations) for operator review, in place "
            "of stating it as chat text. This call blocks pending the operator's decision in "
            "the admin GUI, the same as any state-changing action tool -- send it and wait; "
            "a denial returns {'error': 'denied_by_operator', ...} without running anything."
        ),
        inputSchema=_SUBMIT_PLAN_INPUT_SCHEMA,
        annotations=types.ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False
        ),
    )
    return ExtraTool(tool=tool, handler=run_submit_investigation_plan, requires_approval=True)


def build_submit_investigation_report_tool() -> ExtraTool:
    tool = types.Tool(
        name="submit_investigation_report",
        description=(
            "Submit the closing report for this investigation (observed failure, evidence, "
            "root-cause hypothesis, confidence, action taken, result, remaining uncertainty), "
            "in place of stating it as chat text. This call blocks pending the operator's "
            "decision in the admin GUI, the same as the plan -- send it and wait; a denial "
            "returns {'error': 'denied_by_operator', ...}, and a request for changes returns "
            "{'error': 'changes_requested', 'message': <comment>} without closing the ticket."
        ),
        inputSchema=_SUBMIT_REPORT_INPUT_SCHEMA,
        annotations=types.ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False
        ),
    )
    return ExtraTool(tool=tool, handler=run_submit_investigation_report, requires_approval=True)


__all__ = [
    "build_start_investigate_tool",
    "build_submit_investigation_plan_tool",
    "build_submit_investigation_report_tool",
    "run_start_investigate",
    "run_submit_investigation_plan",
    "run_submit_investigation_report",
]
