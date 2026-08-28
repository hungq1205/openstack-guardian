"""Tests for the submit_investigation_plan/submit_investigation_report
`ExtraTool`s: the investigate-incident skill's replacement for stating a
plan or a closing report as chat text. Covers the `ExtraTool.requires_approval`
wiring in openapi_bridge.py itself (search_failure_patterns never exercised
this path -- it's always unconditionally read-only), not just these two
tools' own handlers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.cmp_admin_mcp.investigation_reporting import (
    build_submit_investigation_plan_tool,
    build_submit_investigation_report_tool,
)
from mcp_servers.openapi_bridge import SpecSource, build_multi_spec_server
from mcp_servers.shared import telemetry

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"

_PLAN_ARGUMENTS = {
    "resource_id": "srv-123",
    "pattern_id": "connection_aborted_transient",
    "evidence": "log line X",
    "hypothesis": "transient connection failure",
    "reasoning": "matches the KB signature",
    "proposed_action": "call rebuild_server",
    "expected_result": "server progresses past ERROR",
    "limitations": "haven't confirmed the connection issue has cleared",
}

_REPORT_ARGUMENTS = {
    "resource_id": "srv-123",
    "observed_failure": "server stuck in ERROR",
    "evidence": "log line X",
    "confidence": "medium",
    "result": "resolved",
    "remaining_uncertainty": "none",
}


def _server(env_prefix: str):
    return build_multi_spec_server(
        "cmp-admin",
        [SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json")],
        env_prefix=env_prefix,
        extra_tools=[
            build_submit_investigation_plan_tool(),
            build_submit_investigation_report_tool(),
        ],
    )


@pytest.mark.asyncio
async def test_both_tools_are_listed() -> None:
    async with create_connected_server_and_client_session(_server("TEST_INVESTIGATION_LIST")) as session:
        tools = await session.list_tools()

    names = {t.name for t in tools.tools}
    assert "submit_investigation_plan" in names
    assert "submit_investigation_report" in names


@pytest.mark.asyncio
async def test_submit_investigation_plan_waits_for_approval_then_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core behavior this feature is built on: a plan submission is
    gated exactly like a state-changing action tool, not fired immediately
    -- proving `ExtraTool(requires_approval=True)` actually reaches
    `instrument_dispatch`, not just that the dataclass field exists."""

    async def _auto_approve(_event_id: int) -> str:
        return "approved"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_approve)

    async with create_connected_server_and_client_session(
        _server("TEST_INVESTIGATION_PLAN_APPROVED")
    ) as session:
        result = await session.call_tool("submit_investigation_plan", _PLAN_ARGUMENTS)

    assert result.isError is False
    assert result.structuredContent == {
        "status": "plan_approved",
        "resource_id": "srv-123",
        "pattern_id": "connection_aborted_transient",
    }


@pytest.mark.asyncio
async def test_submit_investigation_plan_denial_never_runs_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _auto_deny(_event_id: int) -> str:
        return "denied"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_deny)

    async with create_connected_server_and_client_session(
        _server("TEST_INVESTIGATION_PLAN_DENIED")
    ) as session:
        result = await session.call_tool("submit_investigation_plan", _PLAN_ARGUMENTS)

    assert result.structuredContent is not None
    assert result.structuredContent["error"] == "denied_by_operator"


@pytest.mark.asyncio
async def test_submit_investigation_report_waits_for_approval_then_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report is now gated exactly like the plan -- an operator can
    disagree with the write-up even after the underlying fix already ran."""

    async def _auto_approve(_event_id: int) -> str:
        return "approved"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_approve)

    async with create_connected_server_and_client_session(
        _server("TEST_INVESTIGATION_REPORT_APPROVED")
    ) as session:
        result = await session.call_tool("submit_investigation_report", _REPORT_ARGUMENTS)

    assert result.isError is False
    assert result.structuredContent == {"status": "logged", "resource_id": "srv-123"}


@pytest.mark.asyncio
async def test_submit_investigation_report_denial_never_runs_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _auto_deny(_event_id: int) -> str:
        return "denied"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_deny)

    async with create_connected_server_and_client_session(
        _server("TEST_INVESTIGATION_REPORT_DENIED")
    ) as session:
        result = await session.call_tool("submit_investigation_report", _REPORT_ARGUMENTS)

    assert result.structuredContent is not None
    assert result.structuredContent["error"] == "denied_by_operator"
