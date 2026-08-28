"""CRUD for the cmp-admin failure-pattern knowledge base
(`knowledge/failure_patterns.json`), the data `search_failure_patterns` and
`cmp://runbook/{pattern_id}` serve. Every write is validated against the
existing `knowledge/failure_pattern.schema.json` before an atomic file
write -- this stays file-backed rather than moving into SQLite, so it's
still a plain, git-diffable JSON file outside the GUI.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from fastapi import APIRouter, HTTPException

from mcp_servers.cmp_admin_mcp import failure_pattern_matcher as fpm

router = APIRouter(prefix="/failure-patterns", tags=["failure-patterns"])


@router.get("")
async def list_failure_patterns() -> list[dict[str, Any]]:
    return [pattern.raw for pattern in fpm.load_failure_patterns()]


@router.post("", status_code=201)
async def create_failure_pattern(record: dict[str, Any]) -> dict[str, Any]:
    if "id" not in record:
        raise HTTPException(status_code=400, detail="record is missing required field 'id'")
    try:
        fpm.add_failure_pattern(record)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except fpm.DuplicateFailurePatternError as exc:
        raise HTTPException(status_code=409, detail=f"pattern id already exists: {exc}") from exc
    return record


@router.put("/{pattern_id}")
async def update_failure_pattern(pattern_id: str, record: dict[str, Any]) -> dict[str, Any]:
    try:
        fpm.update_failure_pattern(pattern_id, record)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except fpm.UnknownFailurePatternError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pattern: {exc}") from exc
    return record


@router.delete("/{pattern_id}", status_code=204)
async def delete_failure_pattern(pattern_id: str) -> None:
    try:
        fpm.delete_failure_pattern(pattern_id)
    except fpm.UnknownFailurePatternError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pattern: {exc}") from exc


__all__ = ["router"]
