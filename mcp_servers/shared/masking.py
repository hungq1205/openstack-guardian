"""Generic pattern-based PII/secret masking, applied unconditionally before
any log content -- or any tool-call event this project now records -- leaves
this server.

This used to be a hardcoded tuple private to `cmp_logs_mcp` (see that
package's `mask.py`, now a re-export shim over this module). It moved here
because the new telemetry layer (`telemetry.py`) needs the exact same
masking before persisting a tool/resource/prompt call's arguments and result,
across all three MCP servers, not just `cmp-logs`.

Patterns are now DB-backed (see `db.py`'s `masking_patterns` table) so the
admin GUI can add/disable/edit them without a code change, but every read
here degrades safely: if the store can't be reached at all (disk error,
permissions), masking falls back to the original hardcoded EMAIL/IPV4/PHONE
patterns rather than silently masking nothing -- masking is a safety
mechanism, an unreadable audit database must never be the reason PII leaks
through. If the store *is* reachable but the administrator has deliberately
disabled every pattern, that explicit choice is respected (nothing masked).

Patterns are read fresh on every `mask_text`/`mask_value` call rather than
cached in-process: at this project's real scale (one operator, occasional
tool calls) a couple of extra SQLite reads is not a real cost, and it avoids
an entire class of cache-invalidation bugs (multiple MCP server processes
plus a long-lived GUI backend all sharing one store).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mcp_servers.shared import db

logger = logging.getLogger(__name__)

_MAX_SAMPLE_CHARS = 200


@dataclass(frozen=True)
class _CompiledPattern:
    id: int
    name: str
    regex: re.Pattern[str]
    sample_retention_count: int


def _fallback_patterns() -> tuple[_CompiledPattern, ...]:
    """Used only when the store itself can't be reached -- id=-1 marks these
    as not backed by a real `masking_patterns` row, so match samples are
    never recorded for them (there's nowhere to attach the sample to)."""
    return tuple(
        _CompiledPattern(id=-1, name=name, regex=re.compile(pattern), sample_retention_count=0)
        for name, pattern in db.BUILT_IN_MASKING_PATTERNS
    )


def _load_enabled_patterns() -> tuple[_CompiledPattern, ...]:
    try:
        db.ensure_schema()
        conn = db.connect()
    except OSError:
        logger.debug("masking store unreachable, falling back to built-in patterns", exc_info=True)
        return _fallback_patterns()
    try:
        rows = conn.execute(
            "SELECT id, name, regex, sample_retention_count FROM masking_patterns "
            "WHERE enabled = 1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return tuple(
        _CompiledPattern(
            id=row["id"],
            name=row["name"],
            regex=re.compile(row["regex"]),
            sample_retention_count=row["sample_retention_count"],
        )
        for row in rows
    )


def _record_match(pattern: _CompiledPattern, matched_text: str, source: str) -> None:
    if pattern.id < 0:
        return
    try:
        conn = db.connect()
        try:
            now = db.utc_now_iso()
            conn.execute(
                "INSERT INTO mask_match_samples (pattern_id, matched_at, matched_text, source) "
                "VALUES (?, ?, ?, ?)",
                (pattern.id, now, matched_text[:_MAX_SAMPLE_CHARS], source),
            )
            conn.execute(
                "DELETE FROM mask_match_samples WHERE pattern_id = ? AND id NOT IN ("
                "  SELECT id FROM mask_match_samples WHERE pattern_id = ? "
                "  ORDER BY id DESC LIMIT ?"
                ")",
                (pattern.id, pattern.id, pattern.sample_retention_count),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("failed to record mask match sample", exc_info=True)


def _mask_text_with(text: str, patterns: tuple[_CompiledPattern, ...], source: str) -> str:
    masked = text
    for pattern in patterns:

        def _replace(match: re.Match[str], _pattern: _CompiledPattern = pattern) -> str:
            _record_match(_pattern, match.group(0), source)
            return f"[REDACTED:{_pattern.name}]"

        masked = pattern.regex.sub(_replace, masked)
    return masked


def _mask_value_with(value: Any, patterns: tuple[_CompiledPattern, ...], source: str) -> Any:
    if isinstance(value, str):
        return _mask_text_with(value, patterns, source)
    if isinstance(value, Mapping):
        return {key: _mask_value_with(item, patterns, source) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_mask_value_with(item, patterns, source) for item in value]
    return value


def mask_text(text: str, *, source: str = "unknown") -> str:
    """Replace every PII-shaped substring in `text` with a `[REDACTED:KIND]` marker.

    `source` labels where this call came from (e.g. "cmp-logs.search_logs")
    for the masking-match audit trail -- purely descriptive, safe to omit.
    """
    return _mask_text_with(text, _load_enabled_patterns(), source)


def mask_value(value: Any, *, source: str = "unknown") -> Any:
    """Recursively mask every string found in `value` -- dicts, lists, and scalars alike."""
    return _mask_value_with(value, _load_enabled_patterns(), source)


class UnknownPatternError(Exception):
    """No `masking_patterns` row exists for this id."""


class BuiltInPatternError(Exception):
    """A built-in pattern was targeted by an operation only custom patterns allow (delete)."""


@dataclass(frozen=True)
class MaskingPatternRecord:
    """One row of `masking_patterns`, for the admin GUI's masking page --
    distinct from `_CompiledPattern`, which is the lean, compiled-regex shape
    `mask_text`/`mask_value` actually run against."""

    id: int
    name: str
    regex: str
    enabled: bool
    built_in: bool
    sample_retention_count: int


@dataclass(frozen=True)
class MaskMatchSample:
    """One row of `mask_match_samples` -- a real redacted match, for the
    admin GUI's audit-trail viewer. See this module's docstring for the
    privacy tradeoff this table represents."""

    id: int
    matched_at: str
    matched_text: str
    source: str


def list_patterns() -> list[MaskingPatternRecord]:
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, name, regex, enabled, built_in, sample_retention_count "
            "FROM masking_patterns ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        MaskingPatternRecord(
            id=row["id"],
            name=row["name"],
            regex=row["regex"],
            enabled=bool(row["enabled"]),
            built_in=bool(row["built_in"]),
            sample_retention_count=row["sample_retention_count"],
        )
        for row in rows
    ]


def create_pattern(
    name: str, regex: str, *, sample_retention_count: int = 20
) -> MaskingPatternRecord:
    re.compile(regex)  # raises re.error before anything is written
    db.ensure_schema()
    conn = db.connect()
    try:
        now = db.utc_now_iso()
        cursor = conn.execute(
            "INSERT INTO masking_patterns "
            "(name, regex, enabled, built_in, sample_retention_count, created_at, updated_at) "
            "VALUES (?, ?, 1, 0, ?, ?, ?)",
            (name, regex, sample_retention_count, now, now),
        )
        conn.commit()
        pattern_id = cursor.lastrowid
        assert pattern_id is not None
    finally:
        conn.close()
    return MaskingPatternRecord(
        id=pattern_id,
        name=name,
        regex=regex,
        enabled=True,
        built_in=False,
        sample_retention_count=sample_retention_count,
    )


def update_pattern(
    pattern_id: int,
    *,
    regex: str | None = None,
    enabled: bool | None = None,
    sample_retention_count: int | None = None,
) -> MaskingPatternRecord:
    if regex is not None:
        re.compile(regex)  # raises re.error before anything is written
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM masking_patterns WHERE id = ?", (pattern_id,)).fetchone()
        if row is None:
            raise UnknownPatternError(pattern_id)
        new_regex = regex if regex is not None else row["regex"]
        new_enabled = enabled if enabled is not None else bool(row["enabled"])
        new_retention = (
            sample_retention_count
            if sample_retention_count is not None
            else row["sample_retention_count"]
        )
        conn.execute(
            "UPDATE masking_patterns SET regex = ?, enabled = ?, sample_retention_count = ?, "
            "updated_at = ? WHERE id = ?",
            (new_regex, int(new_enabled), new_retention, db.utc_now_iso(), pattern_id),
        )
        conn.commit()
    finally:
        conn.close()
    return MaskingPatternRecord(
        id=pattern_id,
        name=row["name"],
        regex=new_regex,
        enabled=new_enabled,
        built_in=bool(row["built_in"]),
        sample_retention_count=new_retention,
    )


def delete_pattern(pattern_id: int) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT built_in FROM masking_patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if row is None:
            raise UnknownPatternError(pattern_id)
        if row["built_in"]:
            raise BuiltInPatternError(pattern_id)
        conn.execute("DELETE FROM masking_patterns WHERE id = ?", (pattern_id,))
        conn.commit()
    finally:
        conn.close()


def list_samples(pattern_id: int) -> list[MaskMatchSample]:
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, matched_at, matched_text, source FROM mask_match_samples "
            "WHERE pattern_id = ? ORDER BY id DESC",
            (pattern_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        MaskMatchSample(
            id=row["id"],
            matched_at=row["matched_at"],
            matched_text=row["matched_text"],
            source=row["source"],
        )
        for row in rows
    ]


def clear_samples(pattern_id: int) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM mask_match_samples WHERE pattern_id = ?", (pattern_id,))
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "BuiltInPatternError",
    "MaskMatchSample",
    "MaskingPatternRecord",
    "UnknownPatternError",
    "clear_samples",
    "create_pattern",
    "delete_pattern",
    "list_patterns",
    "list_samples",
    "mask_text",
    "mask_value",
    "update_pattern",
]
