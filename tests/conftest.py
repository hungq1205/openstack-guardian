"""Project-wide test fixtures.

Isolates every test's view of the shared SQLite store
(`mcp_servers.shared.db`) into a fresh `tmp_path` -- without this, once the
admin GUI has been run once locally, its real `data/guardian.db`
(config/masking-pattern/event state) would leak into test runs and break
reproducibility.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_shared_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMP_MCP_DATA_DIR", str(tmp_path / "data"))
