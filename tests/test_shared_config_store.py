"""Tests for `mcp_servers.shared.config_store`: the connection-config,
spec-source, and pinned-override tables the admin GUI reads/writes and the
MCP servers' `from_env()`/`_sources()` fall back to when no env var is set.
"""

from __future__ import annotations

from mcp_servers.shared import config_store


def test_get_config_value_is_none_when_nothing_is_configured() -> None:
    assert config_store.get_config_value("CMP_ADMIN_V2", "base_url") is None


def test_set_and_get_connection_config_roundtrip() -> None:
    config_store.set_connection_config(
        "CMP_ADMIN_V2", {"base_url": "https://cmp.example.com", "pat": "secret"}
    )
    assert config_store.get_config_value("CMP_ADMIN_V2", "base_url") == "https://cmp.example.com"
    assert config_store.get_connection_config("CMP_ADMIN_V2") == {
        "base_url": "https://cmp.example.com",
        "pat": "secret",
    }


def test_set_connection_config_overwrites_previous_value() -> None:
    config_store.set_connection_config("CMP_LOGS_ES", {"url": "https://one.example.com"})
    config_store.set_connection_config("CMP_LOGS_ES", {"url": "https://two.example.com"})
    assert config_store.get_config_value("CMP_LOGS_ES", "url") == "https://two.example.com"


def test_spec_sources_are_empty_by_default() -> None:
    assert config_store.get_spec_sources("cmp-admin") == []


def test_spec_sources_crud_roundtrip() -> None:
    source_id = config_store.add_spec_source(
        "cmp-admin", "custom pair", "/specs/custom.json", "/annotations/custom.json"
    )
    sources = config_store.get_spec_sources("cmp-admin")
    assert len(sources) == 1
    assert sources[0].id == source_id
    assert sources[0].name == "custom pair"
    assert sources[0].spec_path == "/specs/custom.json"
    assert sources[0].enabled is True

    config_store.set_spec_source_enabled(source_id, False)
    assert config_store.get_spec_sources("cmp-admin") == []
    assert config_store.get_spec_sources("cmp-admin", enabled_only=False)[0].enabled is False

    config_store.delete_spec_source(source_id)
    assert config_store.get_spec_sources("cmp-admin", enabled_only=False) == []


def test_pinned_overrides_roundtrip() -> None:
    assert config_store.get_pinned_overrides() == {}
    config_store.set_pinned_override("list_volumes", True)
    config_store.set_pinned_override("rebuild_server", False)
    assert config_store.get_pinned_overrides() == {"list_volumes": True, "rebuild_server": False}

    config_store.set_pinned_override("list_volumes", False)
    assert config_store.get_pinned_overrides()["list_volumes"] is False
