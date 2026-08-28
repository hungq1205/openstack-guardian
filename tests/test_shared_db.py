"""Tests for `mcp_servers.shared.db`: schema creation, built-in pattern
seeding, and that `CMP_MCP_DATA_DIR` genuinely controls where the store
lives (the isolation the whole test suite's `conftest.py` fixture relies on).
"""

from __future__ import annotations

from mcp_servers.shared import db


def test_data_dir_respects_env_var(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CMP_MCP_DATA_DIR", str(tmp_path / "custom"))
    assert db.data_dir() == tmp_path / "custom"
    assert db.db_path() == tmp_path / "custom" / "guardian.db"


def test_connect_creates_data_directory_and_file() -> None:
    assert not db.db_path().exists()
    conn = db.connect()
    conn.close()
    assert db.db_path().exists()


def test_ensure_schema_seeds_built_in_masking_patterns() -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name, built_in, enabled FROM masking_patterns ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [row["name"] for row in rows] == ["EMAIL", "IPV4", "PHONE"]
    assert all(row["built_in"] == 1 and row["enabled"] == 1 for row in rows)


def test_ensure_schema_is_idempotent() -> None:
    db.ensure_schema()
    db.ensure_schema()
    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM masking_patterns").fetchone()["n"]
    finally:
        conn.close()
    assert count == 3


def test_ensure_schema_creates_the_tickets_table_and_events_ticket_id_column() -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        ticket_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        indexes = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    finally:
        conn.close()
    assert {"id", "session_id", "title", "resource_id", "state", "created_at", "closed_at"} <= ticket_columns
    assert "ticket_id" in event_columns
    assert "idx_events_ticket_id" in indexes
    assert "idx_tickets_session_id" in indexes


def test_migration_adds_ticket_id_to_a_pre_existing_events_table() -> None:
    """Simulates a `data/guardian.db` created before `ticket_id` existed --
    `ensure_schema()` must add the column (and its index) rather than 500ing
    with "no such column" the next time an event is logged."""
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, server TEXT NOT NULL, "
            "kind TEXT NOT NULL, name TEXT NOT NULL, arguments_json TEXT NOT NULL, "
            "status TEXT NOT NULL, error_message TEXT, result_summary TEXT NOT NULL, "
            "duration_ms REAL NOT NULL, pid INTEGER NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    db.ensure_schema()

    conn = db.connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        # Named-column insert (rather than positional) so this doesn't also
        # need updating every time a column is added -- the point here is
        # only to prove `ticket_id` is genuinely usable after migration, not
        # to re-verify the full column list.
        conn.execute(
            "INSERT INTO events (ts, server, kind, name, arguments_json, status, "
            "error_message, result_summary, duration_ms, pid, ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.utc_now_iso(), "cmp-admin", "tool", "x", "{}", "success", None, "", 0.0, 1, 42),
        )
        conn.commit()
    finally:
        conn.close()
    assert "action" in columns
    assert "ticket_id" in columns
