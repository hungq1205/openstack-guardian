"""Tests for `mcp_servers.shared.masking`: the DB-backed pattern registry,
the fail-safe fallback when the store is unreachable, and the redacted-match
audit trail (`mask_match_samples`) the admin GUI's masking page reads.
"""

from __future__ import annotations

import pytest

from mcp_servers.shared import db
from mcp_servers.shared.masking import mask_text, mask_value


def test_mask_text_redacts_built_in_pattern_kinds() -> None:
    assert "[REDACTED:EMAIL]" in mask_text("reach me at a@example.com")
    assert "[REDACTED:IPV4]" in mask_text("connect to 10.0.0.5")
    assert "[REDACTED:PHONE]" in mask_text("call 555-123-4567")


def test_mask_value_recurses_through_nested_structures() -> None:
    value = {"a": ["reach test@example.com", {"b": "10.0.0.1"}]}
    assert mask_value(value) == {"a": ["reach [REDACTED:EMAIL]", {"b": "[REDACTED:IPV4]"}]}


def test_disabling_a_pattern_stops_it_from_matching() -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        conn.execute("UPDATE masking_patterns SET enabled = 0 WHERE name = 'EMAIL'")
        conn.commit()
    finally:
        conn.close()
    assert "a@example.com" in mask_text("a@example.com")


def test_custom_pattern_gets_applied() -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        now = db.utc_now_iso()
        conn.execute(
            "INSERT INTO masking_patterns "
            "(name, regex, enabled, built_in, sample_retention_count, created_at, updated_at) "
            "VALUES ('SERVER_ID', 'srv-[0-9]+', 1, 0, 20, ?, ?)",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()
    assert mask_text("failure on srv-42") == "failure on [REDACTED:SERVER_ID]"


def test_match_is_recorded_as_a_capped_sample_with_its_source() -> None:
    mask_text("contact ops@example.com now", source="cmp-logs.search_logs")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT matched_text, source FROM mask_match_samples ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["matched_text"] == "ops@example.com"
    assert row["source"] == "cmp-logs.search_logs"


def test_sample_retention_trims_older_matches() -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        conn.execute("UPDATE masking_patterns SET sample_retention_count = 2 WHERE name = 'EMAIL'")
        conn.commit()
    finally:
        conn.close()
    for i in range(5):
        mask_text(f"user{i}@example.com")
    conn = db.connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM mask_match_samples "
            "WHERE pattern_id = (SELECT id FROM masking_patterns WHERE name = 'EMAIL')"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert count == 2


def test_falls_back_to_built_in_patterns_when_store_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("mcp_servers.shared.masking.db.connect", _raise)
    assert "[REDACTED:EMAIL]" in mask_text("still masked: person@example.com")
