"""Tests for the masking pattern/sample CRUD added to
`mcp_servers.shared.masking` for the admin GUI's masking page: create/update/
delete a custom pattern, reject deleting a built-in, list/clear match
samples, and the invalid-regex guard.
"""

from __future__ import annotations

import re

import pytest

from mcp_servers.shared import masking


def test_create_pattern_and_it_gets_applied() -> None:
    record = masking.create_pattern("SERVER_ID", r"srv-[0-9]+")
    assert record.built_in is False
    assert record.enabled is True
    assert masking.mask_text("failure on srv-1") == "failure on [REDACTED:SERVER_ID]"


def test_create_pattern_rejects_invalid_regex() -> None:
    with pytest.raises(re.error):
        masking.create_pattern("BROKEN", r"[unclosed")


def test_update_pattern_can_disable_and_change_regex() -> None:
    record = masking.create_pattern("SERVER_ID", r"srv-[0-9]+")
    updated = masking.update_pattern(record.id, enabled=False)
    assert updated.enabled is False
    assert "srv-1" in masking.mask_text("srv-1")

    masking.update_pattern(record.id, enabled=True, regex=r"task-[0-9]+")
    assert masking.mask_text("task-1") == "[REDACTED:SERVER_ID]"


def test_update_pattern_rejects_unknown_id() -> None:
    with pytest.raises(masking.UnknownPatternError):
        masking.update_pattern(999, enabled=False)


def test_delete_pattern_removes_a_custom_pattern() -> None:
    record = masking.create_pattern("SERVER_ID", r"srv-[0-9]+")
    masking.delete_pattern(record.id)
    names = {p.name for p in masking.list_patterns()}
    assert "SERVER_ID" not in names


def test_delete_pattern_rejects_built_in_patterns() -> None:
    email = next(p for p in masking.list_patterns() if p.name == "EMAIL")
    with pytest.raises(masking.BuiltInPatternError):
        masking.delete_pattern(email.id)


def test_delete_pattern_rejects_unknown_id() -> None:
    with pytest.raises(masking.UnknownPatternError):
        masking.delete_pattern(999)


def test_list_and_clear_samples() -> None:
    email = next(p for p in masking.list_patterns() if p.name == "EMAIL")
    masking.mask_text("contact ops@example.com", source="test")
    assert len(masking.list_samples(email.id)) == 1

    masking.clear_samples(email.id)
    assert masking.list_samples(email.id) == []
