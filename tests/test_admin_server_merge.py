"""Tests for `build_multi_spec_server`/`SpecSource` -- merging all three real
CMP specs into one server under one shared credential set, the foundation
the `cmp-admin` cutover builds on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers.openapi_bridge import (
    OperationSpec,
    ParamSpec,
    SpecSource,
    build_multi_spec_server,
    load_operations,
)

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"
_ALL_SOURCES = [
    SpecSource(_SPECS_DIR / "server.json", _ANNOTATIONS_DIR / "server.json"),
    SpecSource(_SPECS_DIR / "block_storage.json", _ANNOTATIONS_DIR / "block_storage.json"),
    SpecSource(_SPECS_DIR / "network.json", _ANNOTATIONS_DIR / "network.json"),
]


@pytest.mark.asyncio
async def test_merging_all_three_specs_yields_every_real_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every operation across all three specs must be dispatchable on the merged
    server -- proven via call_tool, not list_tools, since the merged server
    uses progressive discovery (see test_tool_discovery.py) and only lists a
    small pinned subset by default. A missing operation would come back as
    `{"error": "unknown_tool", ...}`; anything else (here, `not_configured`,
    since no base URL is set) proves the operation really exists.

    Many of these 104 operations are approval-gated action calls (see
    test_shared_telemetry.py for that behavior itself) -- bypassing the wait
    here keeps this test about existence/dispatchability, not approval."""
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp_servers.shared import telemetry

    async def _auto_approve(_event_id: int) -> str:
        return "approved"

    monkeypatch.setattr(telemetry, "_await_decision", _auto_approve)

    expected = (
        set(load_operations(_SPECS_DIR / "server.json"))
        | set(load_operations(_SPECS_DIR / "block_storage.json"))
        | set(load_operations(_SPECS_DIR / "network.json"))
    )
    assert len(expected) == 104
    server = build_multi_spec_server("cmp-admin", _ALL_SOURCES, env_prefix="TEST_ADMIN_MERGE")

    async with create_connected_server_and_client_session(server) as session:
        for operation_id in expected:
            result = await session.call_tool(operation_id, {})
            assert result.structuredContent != {"error": "unknown_tool", "tool": operation_id}


def test_operation_id_collision_raises_with_both_source_paths() -> None:
    shared_op = OperationSpec(
        operation_id="shared_id",
        method="GET",
        path="/x/{id}",
        summary="x",
        description="x",
        parameters=(
            ParamSpec(name="id", location="path", required=True, schema={"type": "string"}),
        ),
        body_schema=None,
        body_required=False,
    )
    source_a = SpecSource(_SPECS_DIR / "server.json", extra_operations={"shared_id": shared_op})
    source_b = SpecSource(
        _SPECS_DIR / "block_storage.json", extra_operations={"shared_id": shared_op}
    )

    with pytest.raises(ValueError, match="shared_id"):
        build_multi_spec_server("cmp-admin", [source_a, source_b], env_prefix="TEST_COLLISION")


def test_builds_exactly_one_client_for_the_whole_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `CmpApiClient.from_env` call regardless of source count -- the
    whole point of merging is one shared credential set, not N independently
    constructed clients that happen to read the same env prefix. Verified by
    tracking calls rather than by making a real network request."""
    from mcp_servers.openapi_bridge import CmpApiClient

    calls: list[str] = []
    original_from_env = CmpApiClient.from_env.__func__  # type: ignore[attr-defined]

    def _tracking_from_env(cls: type[CmpApiClient], env_prefix: str) -> CmpApiClient:
        calls.append(env_prefix)
        return original_from_env(cls, env_prefix)

    monkeypatch.setattr(CmpApiClient, "from_env", classmethod(_tracking_from_env))

    build_multi_spec_server("cmp-admin", _ALL_SOURCES, env_prefix="TEST_ONE_CLIENT")

    assert calls == ["TEST_ONE_CLIENT"]
