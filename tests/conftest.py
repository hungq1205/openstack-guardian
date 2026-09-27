"""Project-wide test fixtures.

Isolates every test's view of the shared SQLite store (`guardian_platform.db`,
an editable-path dependency -- see pyproject.toml) into a fresh `tmp_path` --
without this, once the admin GUI has been run once locally, its real
`data/guardian.db` (config/masking-pattern/event state) would leak into test
runs and break reproducibility. Same reasoning for `.mcp.json`: without this,
any test that exercises `admin_gui.backend.mcp_config_sync.sync_connection`
(directly, or indirectly through the mcp_servers router) would rewrite this
repo's real, hand-maintained `.mcp.json`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_shared_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDIAN_PLATFORM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GUARDIAN_PLATFORM_MCP_JSON_PATH", str(tmp_path / ".mcp.json"))
