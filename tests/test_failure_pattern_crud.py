"""Tests for the failure-pattern KB CRUD added to `failure_pattern_matcher.py`
for the admin GUI's KB editor: schema validation, duplicate-id rejection,
atomic writes, and cache invalidation.

Every test operates on a `tmp_path` copy of the real `failure_patterns.json`
-- never the real project file -- passed explicitly via each function's
`path=` parameter.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from mcp_servers.cmp_admin_mcp import failure_pattern_matcher as fpm

_REAL_PATH = (
    Path(__file__).resolve().parent.parent
    / "mcp_servers"
    / "cmp_admin_mcp"
    / "knowledge"
    / "failure_patterns.json"
)

_VALID_NEW_PATTERN = {
    "id": "test_pattern",
    "signature_pattern": "something failed for {resource_id}",
    "cause": "a made-up cause for testing",
    "instruction": "test instruction for handling this pattern",
    "source_reference": "test suite",
}


@pytest.fixture
def kb_path(tmp_path) -> Path:
    destination = tmp_path / "failure_patterns.json"
    shutil.copy(_REAL_PATH, destination)
    yield destination
    fpm.reload_failure_patterns()


def test_add_failure_pattern_appends_and_validates(kb_path: Path) -> None:
    fpm.add_failure_pattern(_VALID_NEW_PATTERN, path=kb_path)
    records = json.loads(kb_path.read_text(encoding="utf-8"))
    assert any(r["id"] == "test_pattern" for r in records)


def test_add_failure_pattern_rejects_invalid_record(kb_path: Path) -> None:
    invalid = {"id": "missing_fields"}
    with pytest.raises(jsonschema.ValidationError):
        fpm.add_failure_pattern(invalid, path=kb_path)


def test_add_failure_pattern_rejects_duplicate_id(kb_path: Path) -> None:
    with pytest.raises(fpm.DuplicateFailurePatternError):
        fpm.add_failure_pattern({**_VALID_NEW_PATTERN, "id": "volume_status_drift"}, path=kb_path)


def test_update_failure_pattern_replaces_the_record(kb_path: Path) -> None:
    updated = {**_VALID_NEW_PATTERN, "id": "volume_status_drift", "cause": "a different cause"}
    fpm.update_failure_pattern("volume_status_drift", updated, path=kb_path)
    records = json.loads(kb_path.read_text(encoding="utf-8"))
    record = next(r for r in records if r["id"] == "volume_status_drift")
    assert record["cause"] == "a different cause"


def test_update_failure_pattern_rejects_unknown_id(kb_path: Path) -> None:
    with pytest.raises(fpm.UnknownFailurePatternError):
        fpm.update_failure_pattern("does_not_exist", _VALID_NEW_PATTERN, path=kb_path)


def test_delete_failure_pattern_removes_the_record(kb_path: Path) -> None:
    fpm.delete_failure_pattern("volume_status_drift", path=kb_path)
    records = json.loads(kb_path.read_text(encoding="utf-8"))
    assert all(r["id"] != "volume_status_drift" for r in records)


def test_delete_failure_pattern_rejects_unknown_id(kb_path: Path) -> None:
    with pytest.raises(fpm.UnknownFailurePatternError):
        fpm.delete_failure_pattern("does_not_exist", path=kb_path)


def test_writes_are_reflected_after_reload(kb_path: Path) -> None:
    before = fpm.load_failure_patterns(kb_path)
    assert not any(p.id == "test_pattern" for p in before)

    fpm.add_failure_pattern(_VALID_NEW_PATTERN, path=kb_path)

    after = fpm.load_failure_patterns(kb_path)
    assert any(p.id == "test_pattern" for p in after)


def test_real_project_file_is_untouched() -> None:
    """Guard against a test accidentally operating on the real file: every
    CRUD test above must pass an explicit tmp_path via `path=`."""
    real_ids_before = {p["id"] for p in json.loads(_REAL_PATH.read_text(encoding="utf-8"))}
    assert "test_pattern" not in real_ids_before
