"""Tests for the config-store fallback added to `CmpApiClient.from_env`,
`ElasticsearchLogsClient.from_env`, `NotifyClient.from_env`, and
`cmp_admin_mcp.main._sources()`/`build_admin_server()`'s pinned-override
overlay -- proving env vars still win when set, the config store fills in
when they're not, and defaults are unchanged when nothing is configured at
all (the zero-behavior-change guarantee the migration plan requires).
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.cmp_admin_mcp.main import build_admin_server
from mcp_servers.cmp_logs_mcp.client import ElasticsearchLogsClient
from mcp_servers.cmp_notify_mcp.client import NotifyClient
from mcp_servers.openapi_bridge import CmpApiClient
from mcp_servers.shared import config_store

_ADMIN_ENV_VARS = (
    "CMP_ADMIN_V2_BASE_URL",
    "CMP_ADMIN_V2_PAT",
    "CMP_ADMIN_V2_USERNAME",
    "CMP_ADMIN_V2_PASSWORD",
)
_LOGS_ENV_VARS = (
    "CMP_LOGS_ES_URL",
    "CMP_LOGS_ES_INDEX",
    "CMP_LOGS_ES_API_KEY",
    "CMP_LOGS_ES_USERNAME",
    "CMP_LOGS_ES_PASSWORD",
)


@pytest.fixture(autouse=True)
def _clean_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*_ADMIN_ENV_VARS, *_LOGS_ENV_VARS, "CMP_NOTIFY_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


def test_cmp_api_client_defaults_are_unchanged_when_nothing_is_configured() -> None:
    client = CmpApiClient.from_env("CMP_ADMIN_V2")
    assert client.base_url == ""
    assert client.auth_header == {}


def test_cmp_api_client_falls_back_to_config_store() -> None:
    config_store.set_connection_config(
        "CMP_ADMIN_V2", {"base_url": "https://cmp.example.com/", "pat": "tok"}
    )
    client = CmpApiClient.from_env("CMP_ADMIN_V2")
    assert client.base_url == "https://cmp.example.com"
    assert client.auth_header == {"Authorization": "token tok"}


def test_cmp_api_client_env_var_wins_over_config_store(monkeypatch: pytest.MonkeyPatch) -> None:
    config_store.set_connection_config(
        "CMP_ADMIN_V2", {"base_url": "https://from-store.example.com"}
    )
    monkeypatch.setenv("CMP_ADMIN_V2_BASE_URL", "https://from-env.example.com")
    client = CmpApiClient.from_env("CMP_ADMIN_V2")
    assert client.base_url == "https://from-env.example.com"


def test_elasticsearch_client_falls_back_to_config_store() -> None:
    config_store.set_connection_config(
        "CMP_LOGS_ES", {"url": "https://es.example.com", "index": "logs-*"}
    )
    client = ElasticsearchLogsClient.from_env()
    assert client.base_url == "https://es.example.com"
    assert client.index == "logs-*"


def test_notify_client_falls_back_to_config_store() -> None:
    config_store.set_connection_config("CMP_NOTIFY", {"webhook_url": "https://hooks.example.com/x"})
    client = NotifyClient.from_env()
    assert client.webhook_url == "https://hooks.example.com/x"


def test_notify_client_default_is_unchanged_when_nothing_is_configured() -> None:
    assert NotifyClient.from_env().webhook_url == ""


@pytest.mark.asyncio
async def test_admin_server_uses_hardcoded_sources_when_store_is_empty() -> None:
    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("list_compute_nodes", {})
    assert result.structuredContent != {"error": "unknown_tool", "tool": "list_compute_nodes"}


def test_sources_adds_configured_spec_sources_on_top_of_the_3_built_ins(tmp_path) -> None:
    """`_sources()` itself, not the full `build_admin_server()` -- a
    GUI-configured spec source is always additive, never a replacement for
    the 3 built-in pairs (see the docstring on `sources()`): some resource
    templates index built-in-only operations by name and fail loudly if
    they go missing (`cmp_admin_mcp/resources.py`), so dropping them the
    moment any row exists is a real bug, not a design tradeoff."""
    from mcp_servers.cmp_admin_mcp.main import sources

    spec_path = tmp_path / "custom.json"
    annotations_path = tmp_path / "custom.annotations.json"
    spec_path.write_text(
        '{"openapi": "3.1.0", "info": {"title": "custom", "version": "1"}, '
        '"paths": {"/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}}}',
        encoding="utf-8",
    )
    annotations_path.write_text("{}", encoding="utf-8")
    config_store.add_spec_source("cmp-admin", "custom pair", str(spec_path), str(annotations_path))

    result = sources()

    assert len(result) == 4
    assert result[-1].spec_path == spec_path
    assert result[-1].annotations_path == annotations_path


def test_sources_edited_domain_takes_that_built_in_slot_not_a_collision(tmp_path) -> None:
    """A spec source row with `domain="server"` (an edit made through the
    admin GUI's Spec Sources page -- see `PUT /spec-sources/built-in/{domain}`)
    takes the built-in server pair's slot instead of colliding with it."""
    from mcp_servers.cmp_admin_mcp.main import sources

    spec_path = tmp_path / "custom_server.json"
    spec_path.write_text(
        '{"openapi": "3.1.0", "info": {"title": "custom", "version": "1"}, '
        '"paths": {"/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}}}',
        encoding="utf-8",
    )
    config_store.add_spec_source("cmp-admin", "my server", str(spec_path), domain="server")

    result = sources()

    assert len(result) == 3
    assert result[0].spec_path == spec_path  # took the "server" slot, first position
    assert result[1].spec_path.name == "block_storage.json"  # other 2 built-ins untouched
    assert result[2].spec_path.name == "network.json"


def test_sources_disabled_domain_is_dropped_entirely(tmp_path) -> None:
    """Explicitly disabling a built-in domain (the "remove" action on the
    Spec Sources page) drops it rather than falling back to the default --
    that's the whole point of being able to remove one."""
    from mcp_servers.cmp_admin_mcp.main import sources

    config_store.add_spec_source(
        "cmp-admin", "network", str(tmp_path / "unused.json"), domain="network"
    )
    config_store.set_spec_source_enabled(
        config_store.get_spec_sources("cmp-admin", enabled_only=False)[0].id, False
    )

    result = sources()

    assert len(result) == 2
    names = {p.spec_path.name for p in result}
    assert names == {"server.json", "block_storage.json"}


@pytest.mark.asyncio
async def test_admin_server_applies_pinned_overrides() -> None:
    # Use delete_server (action tool, not pinned by default) and rebuild_server (action tool, pinned by default)
    config_store.set_pinned_override("delete_server", True)
    config_store.set_pinned_override("rebuild_server", False)

    server = build_admin_server()
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()

    names = {t.name for t in tools.tools}
    assert "delete_server" in names  # pinned via override
    assert "rebuild_server" not in names  # unpinned via override
