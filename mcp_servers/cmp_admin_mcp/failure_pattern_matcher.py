"""Match free-form error text against the curated failure-pattern knowledge
base (knowledge/failure_patterns.json) and expose it as `search_failure_patterns`.

Structured, queryable data the `assemble_log` prompt consults
mid-investigation -- never executes or decides anything itself, only answers
"is this a known pattern, and what does it say." The template-to-regex
matching technique is adapted from the archived v1 harness's KB matcher
(archive/opensre/archive/cmp_agent_harness_v1/tools_system_troubleshooting_kb/matcher.py).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from mcp import types

from mcp_servers.openapi_bridge import ExtraTool

_DEFAULT_PATH = Path(__file__).resolve().parent / "knowledge" / "failure_patterns.json"
_SCHEMA_PATH = Path(__file__).resolve().parent / "knowledge" / "failure_pattern.schema.json"
_PLACEHOLDER = re.compile(r"\{[a-zA-Z0-9_]+\}|\.\.\.")


@dataclass(frozen=True)
class FailurePattern:
    """One curated failure-signature record. `raw` is the exact parsed JSON
    object for this entry -- passed through as-is rather than modeled field by
    field, since every consumer here wants the full record (id,
    signature_pattern, cause, instruction, source_reference; see
    knowledge/failure_pattern.schema.json), not a lossy projection of it."""

    id: str
    raw: dict[str, Any]


@lru_cache(maxsize=1)
def load_failure_patterns(path: Path | None = None) -> tuple[FailurePattern, ...]:
    """`path=None` (the normal case) resolves `_DEFAULT_PATH` at call time,
    not at import time -- so `reload_failure_patterns()` picks up both a
    rewritten file *and* a monkeypatched `_DEFAULT_PATH` (the admin GUI's
    router tests redirect it to a throwaway copy rather than risk writing to
    the real project file)."""
    resolved = path if path is not None else _DEFAULT_PATH
    raw_entries = json.loads(resolved.read_text(encoding="utf-8"))
    return tuple(FailurePattern(id=entry["id"], raw=entry) for entry in raw_entries)


def get_pattern_by_id(pattern_id: str, path: Path | None = None) -> FailurePattern | None:
    """Direct lookup by id -- used by the cmp://runbook/{pattern_id} resource."""
    return next(
        (pattern for pattern in load_failure_patterns(path) if pattern.id == pattern_id), None
    )


def reload_failure_patterns() -> None:
    """Clear the in-process cache after `failure_patterns.json` is rewritten
    (e.g. by the admin GUI's KB editor) so this process's own view refreshes
    without a restart."""
    load_failure_patterns.cache_clear()
    _compiled_patterns.cache_clear()


class UnknownFailurePatternError(Exception):
    """No entry with this id exists in `failure_patterns.json`."""


class DuplicateFailurePatternError(Exception):
    """An entry with this id already exists in `failure_patterns.json`."""


def _validate_failure_pattern(record: dict[str, Any]) -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(record, schema)


def _write_failure_patterns(records: list[dict[str, Any]], path: Path | None = None) -> None:
    """Atomic write (temp file + `os.replace`) so a crash mid-write can never
    leave `failure_patterns.json` truncated or half-written."""
    resolved = path if path is not None else _DEFAULT_PATH
    fd, tmp_name = tempfile.mkstemp(dir=resolved.parent, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, resolved)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    reload_failure_patterns()


def add_failure_pattern(record: dict[str, Any], path: Path | None = None) -> None:
    _validate_failure_pattern(record)
    resolved = path if path is not None else _DEFAULT_PATH
    records = json.loads(resolved.read_text(encoding="utf-8"))
    if any(existing["id"] == record["id"] for existing in records):
        raise DuplicateFailurePatternError(record["id"])
    records.append(record)
    _write_failure_patterns(records, resolved)


def update_failure_pattern(
    pattern_id: str, record: dict[str, Any], path: Path | None = None
) -> None:
    _validate_failure_pattern(record)
    resolved = path if path is not None else _DEFAULT_PATH
    records = json.loads(resolved.read_text(encoding="utf-8"))
    for index, existing in enumerate(records):
        if existing["id"] == pattern_id:
            records[index] = record
            _write_failure_patterns(records, resolved)
            return
    raise UnknownFailurePatternError(pattern_id)


def delete_failure_pattern(pattern_id: str, path: Path | None = None) -> None:
    resolved = path if path is not None else _DEFAULT_PATH
    records = json.loads(resolved.read_text(encoding="utf-8"))
    filtered = [existing for existing in records if existing["id"] != pattern_id]
    if len(filtered) == len(records):
        raise UnknownFailurePatternError(pattern_id)
    _write_failure_patterns(filtered, resolved)


def _compile_signature(signature_pattern: str) -> re.Pattern[str]:
    """Compile one `signature_pattern` template into a regex: literal text
    escaped, each `{placeholder}` (or a literal `...` elision, used by one
    entry that predates the `{name}` convention) replaced with a wildcard."""
    parts = _PLACEHOLDER.split(signature_pattern)
    escaped_parts = [re.escape(part) for part in parts]
    return re.compile(r".+?".join(escaped_parts), re.IGNORECASE | re.DOTALL)


@lru_cache(maxsize=1)
def _compiled_patterns(
    path: Path | None = None,
) -> tuple[tuple[FailurePattern, re.Pattern[str]], ...]:
    return tuple(
        (pattern, _compile_signature(pattern.raw["signature_pattern"]))
        for pattern in load_failure_patterns(path)
    )


def find_matching_patterns(error_text: str, path: Path | None = None) -> list[FailurePattern]:
    """Every curated pattern whose signature template matches `error_text` --
    a search against free-form text, not a single best guess, so a caller
    sees every candidate rather than having one silently picked for them."""
    return [pattern for pattern, regex in _compiled_patterns(path) if regex.search(error_text)]


_SEARCH_FAILURE_PATTERNS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "error_text": {
            "type": "string",
            "description": (
                "The raw error message or log line to match against known failure-pattern "
                "signatures, e.g. text returned by search_logs or pasted by an administrator. "
                "Pass the whole line start to end, not just the exception fragment -- e.g. use "
                "\"ERROR celery.server_creator server_creator_execution 04ec... Error generating "
                "server ...: RemoteDisconnected(...) @timestamp:...\" rather than trimming it "
                "down to just \"RemoteDisconnected(...)\"."
            ),
        },
    },
    "required": ["error_text"],
}

_FAILURE_PATTERN_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "signature_pattern": {"type": "string"},
        "cause": {"type": "string"},
        "instruction": {"type": "string"},
        "source_reference": {"type": "string"},
    },
    "required": ["id", "signature_pattern", "cause", "instruction", "source_reference"],
}

_SEARCH_FAILURE_PATTERNS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "matches": {"type": "array", "items": _FAILURE_PATTERN_RECORD_SCHEMA},
    },
    "required": ["matches"],
}


def run_search_failure_patterns(arguments: dict[str, Any]) -> dict[str, Any]:
    error_text = str(arguments.get("error_text") or "")
    matches = find_matching_patterns(error_text)
    return {"matches": [pattern.raw for pattern in matches]}


def build_search_failure_patterns_tool() -> ExtraTool:
    tool = types.Tool(
        name="search_failure_patterns",
        description=(
            "Search the curated failure-pattern knowledge base for entries whose signature "
            "matches free-form error text. Returns each match's full record -- cause, "
            "verification steps, designated tools, and remediation instruction -- or "
            "{'matches': []} if nothing in the knowledge base matches. A signature match is "
            "a hypothesis to verify against current real state, never a confirmed diagnosis "
            "to act on directly -- follow the record's own verification step first when it "
            "has one."
        ),
        inputSchema=_SEARCH_FAILURE_PATTERNS_INPUT_SCHEMA,
        outputSchema=_SEARCH_FAILURE_PATTERNS_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(
            readOnlyHint=True, idempotentHint=True, destructiveHint=False
        ),
    )
    return ExtraTool(tool=tool, handler=run_search_failure_patterns)


__all__ = [
    "DuplicateFailurePatternError",
    "FailurePattern",
    "UnknownFailurePatternError",
    "add_failure_pattern",
    "build_search_failure_patterns_tool",
    "delete_failure_pattern",
    "find_matching_patterns",
    "get_pattern_by_id",
    "load_failure_patterns",
    "reload_failure_patterns",
    "run_search_failure_patterns",
    "update_failure_pattern",
]
