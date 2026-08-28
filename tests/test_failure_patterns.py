"""Tests for the curated failure-pattern knowledge base: the matcher functions
directly, and the `search_failure_patterns` tool wired onto a server via
`ExtraTool`. Covers both of the reference scenarios from the original CSV
troubleshooting compilation by name -- a volume-status-drift error and a
transient Nova/core connection drop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers.cmp_admin_mcp.failure_pattern_matcher import (
    build_search_failure_patterns_tool,
    find_matching_patterns,
    get_pattern_by_id,
    load_failure_patterns,
    run_search_failure_patterns,
)
from mcp_servers.openapi_bridge import SpecSource, build_multi_spec_server

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"

_VOLUME_STATUS_DRIFT_ERROR = (
    "ERROR celery.server_creator server_creator_execution "
    "11111111-1111-1111-1111-111111111111 Error generating server "
    "22222222-2222-2222-2222-222222222222: Invalid volume: Invalid input "
    "received: Invalid volume: Volume 3fa85f64-5717-4562-b3fc-2c963f66afa6 "
    "status must be available or downloading to reserve, but the current "
    "status is in-use."
)
_CONNECTION_ABORTED_ERROR = (
    "ERROR celery.server_creator server_creator_execution "
    "11111111-1111-1111-1111-111111111111 Error generating server "
    "22222222-2222-2222-2222-222222222222: Unable to establish connection to "
    "https://core.example.com/v2/servers: ('Connection aborted.', "
    "RemoteDisconnected('Remote end closed connection without response')) "
    "@timestamp:Jan 1, 2026 @ 00:00:00.000."
)


def test_load_failure_patterns_loads_all_twelve_curated_entries() -> None:
    patterns = load_failure_patterns()
    assert len(patterns) == 12
    assert {pattern.id for pattern in patterns} == {
        "volume_status_drift",
        "connection_aborted_transient",
        "gateway_timeout_transient",
        "block_device_not_bootable_transient",
        "image_not_active",
        "invalid_image_identifier",
        "port_status_drift",
        "no_available_ip",
        "invalid_security_group_none",
        "compute_capacity_exhaustion",
        "max_retries_exceeded_all_hosts",
        "volume_not_found",
    }
    for pattern in patterns:
        assert hasattr(pattern.raw, "__getitem__")
        assert "signature_pattern" in pattern.raw
        assert "instruction" in pattern.raw


def test_find_matching_patterns_matches_volume_status_drift() -> None:
    """Confirms the matcher finds the right pattern by signature."""
    matches = find_matching_patterns(_VOLUME_STATUS_DRIFT_ERROR)
    assert [pattern.id for pattern in matches] == ["volume_status_drift"]
    assert "instruction" in matches[0].raw
    assert "signature_pattern" in matches[0].raw


def test_find_matching_patterns_matches_connection_aborted_transient() -> None:
    matches = find_matching_patterns(_CONNECTION_ABORTED_ERROR)
    assert [pattern.id for pattern in matches] == ["connection_aborted_transient"]


def test_find_matching_patterns_returns_empty_for_unrecognized_text() -> None:
    assert find_matching_patterns("some completely unrelated error message") == []


def test_get_pattern_by_id_returns_the_full_record() -> None:
    pattern = get_pattern_by_id("port_status_drift")
    assert pattern is not None
    assert pattern.raw["instruction"]
    assert pattern.raw["signature_pattern"].startswith("ERROR celery.server_creator")


def test_get_pattern_by_id_returns_none_for_an_unknown_id() -> None:
    assert get_pattern_by_id("not_a_real_pattern") is None


def test_run_search_failure_patterns_wraps_matches_in_a_matches_list() -> None:
    expected_pattern = get_pattern_by_id("volume_status_drift")
    assert expected_pattern is not None
    assert run_search_failure_patterns({"error_text": _VOLUME_STATUS_DRIFT_ERROR}) == {
        "matches": [expected_pattern.raw]
    }


def test_run_search_failure_patterns_returns_empty_matches_for_no_hit() -> None:
    assert run_search_failure_patterns({"error_text": "nothing recognizable here"}) == {
        "matches": []
    }


@pytest.mark.asyncio
async def test_search_failure_patterns_tool_is_listed_and_callable_end_to_end() -> None:
    """Proves the ExtraTool wiring itself: search_failure_patterns has no
    backing OpenAPI operation at all, yet is unconditionally listed and
    dispatches correctly through the exact same server construction path as
    every spec-derived tool."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_multi_spec_server(
        "cmp-admin",
        [SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json")],
        env_prefix="TEST_FAILURE_PATTERNS",
        extra_tools=[build_search_failure_patterns_tool()],
    )

    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert "search_failure_patterns" in {t.name for t in tools.tools}

        result = await session.call_tool(
            "search_failure_patterns", {"error_text": _VOLUME_STATUS_DRIFT_ERROR}
        )

    assert result.isError is False
    assert result.structuredContent is not None
    matches = result.structuredContent["matches"]
    assert len(matches) == 1
    assert matches[0]["id"] == "volume_status_drift"


@pytest.mark.asyncio
async def test_search_failure_patterns_returns_empty_matches_when_nothing_matches() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_multi_spec_server(
        "cmp-admin",
        [SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json")],
        env_prefix="TEST_FAILURE_PATTERNS_EMPTY",
        extra_tools=[build_search_failure_patterns_tool()],
    )

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "search_failure_patterns", {"error_text": "totally unrelated text"}
        )

    assert result.isError is False
    assert result.structuredContent == {"matches": []}
