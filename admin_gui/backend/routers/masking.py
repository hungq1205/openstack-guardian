"""CRUD for masking patterns (built-in + custom) and the redacted-match
audit trail. Note for whoever builds the frontend page for this: the
`/patterns/{id}/samples` route returns real matched PII/secret text, capped
and retention-limited but genuinely at rest -- the page should make that
visible (a reveal toggle, a warning), not just render it like any other
table.
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_servers.shared import masking

router = APIRouter(prefix="/masking", tags=["masking"])


class PatternOut(BaseModel):
    id: int
    name: str
    regex: str
    enabled: bool
    built_in: bool
    sample_retention_count: int


class PatternCreate(BaseModel):
    name: str
    regex: str
    sample_retention_count: int = 20


class PatternUpdate(BaseModel):
    regex: str | None = None
    enabled: bool | None = None
    sample_retention_count: int | None = None


class SampleOut(BaseModel):
    id: int
    matched_at: str
    matched_text: str
    source: str


def _to_out(record: masking.MaskingPatternRecord) -> PatternOut:
    return PatternOut(
        id=record.id,
        name=record.name,
        regex=record.regex,
        enabled=record.enabled,
        built_in=record.built_in,
        sample_retention_count=record.sample_retention_count,
    )


@router.get("/patterns", response_model=list[PatternOut])
async def list_patterns() -> list[PatternOut]:
    return [_to_out(record) for record in masking.list_patterns()]


@router.post("/patterns", response_model=PatternOut, status_code=201)
async def create_pattern(body: PatternCreate) -> PatternOut:
    try:
        record = masking.create_pattern(
            body.name, body.regex, sample_retention_count=body.sample_retention_count
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"pattern name already exists: {body.name}"
        ) from exc
    return _to_out(record)


@router.put("/patterns/{pattern_id}", response_model=PatternOut)
async def update_pattern(pattern_id: int, body: PatternUpdate) -> PatternOut:
    try:
        record = masking.update_pattern(
            pattern_id,
            regex=body.regex,
            enabled=body.enabled,
            sample_retention_count=body.sample_retention_count,
        )
    except masking.UnknownPatternError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pattern: {pattern_id}") from exc
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc
    return _to_out(record)


@router.delete("/patterns/{pattern_id}", status_code=204)
async def delete_pattern(pattern_id: int) -> None:
    try:
        masking.delete_pattern(pattern_id)
    except masking.UnknownPatternError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pattern: {pattern_id}") from exc
    except masking.BuiltInPatternError as exc:
        raise HTTPException(
            status_code=400, detail="built-in patterns cannot be deleted, only disabled"
        ) from exc


@router.get("/patterns/{pattern_id}/samples", response_model=list[SampleOut])
async def list_samples(pattern_id: int) -> list[SampleOut]:
    return [
        SampleOut(id=s.id, matched_at=s.matched_at, matched_text=s.matched_text, source=s.source)
        for s in masking.list_samples(pattern_id)
    ]


@router.delete("/patterns/{pattern_id}/samples", status_code=204)
async def clear_samples(pattern_id: int) -> None:
    masking.clear_samples(pattern_id)


__all__ = ["router"]
