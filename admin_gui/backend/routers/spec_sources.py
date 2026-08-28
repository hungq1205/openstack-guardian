"""CRUD for which OpenAPI spec/annotation JSON pairs feed cmp-admin.

Every domain in `mcp_servers.cmp_admin_mcp.main.BUILT_IN_SPEC_DOMAINS` is
always listed here, even before anyone has touched it -- editable and
toggleable like any other row, via the `/built-in/{domain}` routes, which
materialize a `spec_sources` override row on first edit rather than
requiring one to already exist. Each domain starts enabled or disabled per
`main.domain_enabled_by_default` (the 3 original built-ins are on;
`server_v1`, an alternate API version of the `server` domain, ships off).
Domains aren't mutually exclusive with each other -- enabling `server_v1`
alongside `server` doesn't collide (different operationId namespaces);
toggling one off and the other on is just how an admin "switches" between
them. On top of these, an administrator can add purely additive extra pairs
through the plain `POST`/`PUT`/`DELETE /{id}` routes.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from mcp_servers.cmp_admin_mcp.main import (
    BUILT_IN_SPEC_DOMAINS,
    default_spec_source,
    domain_enabled_by_default,
)
from mcp_servers.shared import config_store

router = APIRouter(prefix="/spec-sources", tags=["spec-sources"])

_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


class SpecSourceOut(BaseModel):
    id: int | None
    """`None` for a built-in domain that has never been edited -- a
    synthesized row showing the hardcoded default, not yet backed by a real
    `spec_sources` table row."""
    server: str
    name: str
    domain: str | None
    spec_path: str
    annotations_path: str | None
    enabled: bool
    sort_order: int


class EnabledUpdate(BaseModel):
    enabled: bool


def _to_out(row: config_store.SpecSourceRow) -> SpecSourceOut:
    return SpecSourceOut(
        id=row.id,
        server=row.server,
        name=row.name,
        domain=row.domain,
        spec_path=row.spec_path,
        annotations_path=row.annotations_path,
        enabled=row.enabled,
        sort_order=row.sort_order,
    )


def _virtual_built_in(server: str, domain: str, sort_order: int) -> SpecSourceOut:
    default = default_spec_source(domain)
    return SpecSourceOut(
        id=None,
        server=server,
        name=domain,
        domain=domain,
        spec_path=str(default.spec_path),
        annotations_path=str(default.annotations_path) if default.annotations_path else None,
        enabled=domain_enabled_by_default(domain),
        sort_order=sort_order,
    )


async def _save_upload(upload: UploadFile) -> Path:
    """Each upload gets its own directory named with a random id (so two
    uploads can never collide), but keeps its own original filename inside
    it -- `spec_path`/`annotations_path` then read as e.g.
    `.../a1b2c3d4/network.json`, not an opaque hex blob, which is what an
    admin actually needs to recognize which file is which later."""
    original_name = Path(upload.filename or "upload.json").name
    if not original_name or original_name in {".", ".."}:
        original_name = "upload.json"
    destination_dir = _UPLOAD_DIR / uuid.uuid4().hex
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / original_name
    contents = await upload.read()
    try:
        json.loads(contents)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"{upload.filename}: not valid JSON ({exc})"
        ) from exc
    destination.write_bytes(contents)
    return destination


@router.get("", response_model=list[SpecSourceOut])
async def list_spec_sources(server: str) -> list[SpecSourceOut]:
    rows = config_store.get_spec_sources(server, enabled_only=False)
    by_domain = {row.domain: row for row in rows if row.domain is not None}
    result = [
        _to_out(by_domain[domain]) if domain in by_domain else _virtual_built_in(server, domain, index)
        for index, domain in enumerate(BUILT_IN_SPEC_DOMAINS)
    ]
    result += [_to_out(row) for row in rows if row.domain is None]
    return result


@router.post("", response_model=SpecSourceOut, status_code=201)
async def add_spec_source(
    server: Annotated[str, Form()],
    name: Annotated[str, Form()],
    spec_file: Annotated[UploadFile, File()],
    annotations_file: Annotated[UploadFile | None, File()] = None,
) -> SpecSourceOut:
    """Add a purely additive extra pair, alongside the 3 built-ins -- to
    edit one of the built-ins itself, use `PUT /built-in/{domain}`."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    spec_path = await _save_upload(spec_file)
    annotations_path = (
        await _save_upload(annotations_file) if annotations_file is not None else None
    )
    source_id = config_store.add_spec_source(
        server, name.strip(), str(spec_path), str(annotations_path) if annotations_path else None
    )
    return SpecSourceOut(
        id=source_id,
        server=server,
        name=name.strip(),
        domain=None,
        spec_path=str(spec_path),
        annotations_path=str(annotations_path) if annotations_path else None,
        enabled=True,
        sort_order=0,
    )


@router.put("/built-in/{domain}", response_model=SpecSourceOut)
async def update_built_in_spec_source(
    domain: str,
    server: Annotated[str, Form()],
    name: Annotated[str | None, Form()] = None,
    enabled: Annotated[bool | None, Form()] = None,
    spec_file: Annotated[UploadFile | None, File()] = None,
    annotations_file: Annotated[UploadFile | None, File()] = None,
) -> SpecSourceOut:
    """Edit (upload a replacement file for) or disable one of the 3
    built-in domains -- materializes a `spec_sources` override row on first
    call, updates it on later ones. Uploading no file just changes `name`/
    `enabled`, keeping whatever spec/annotations are already in effect."""
    if domain not in BUILT_IN_SPEC_DOMAINS:
        raise HTTPException(status_code=404, detail=f"unknown built-in domain: {domain}")

    uploaded_spec_path = str(await _save_upload(spec_file)) if spec_file is not None else None
    uploaded_annotations_path = (
        str(await _save_upload(annotations_file)) if annotations_file is not None else None
    )
    existing = next(
        (row for row in config_store.get_spec_sources(server, enabled_only=False) if row.domain == domain),
        None,
    )
    default = default_spec_source(domain)
    spec_path = uploaded_spec_path or (existing.spec_path if existing else str(default.spec_path))
    annotations_path = uploaded_annotations_path or (
        existing.annotations_path
        if existing
        else (str(default.annotations_path) if default.annotations_path else None)
    )
    # Uploading a new file shouldn't silently flip whether the domain is
    # active -- preserve the existing row's enabled state, or this domain's
    # own shipped default (not just "always on") the first time it's
    # materialized.
    effective_enabled = (
        enabled if enabled is not None else (existing.enabled if existing else domain_enabled_by_default(domain))
    )
    row = config_store.upsert_domain_spec_source(
        server,
        domain,
        name=name,
        spec_path=spec_path,
        annotations_path=annotations_path,
        enabled=effective_enabled,
    )
    return _to_out(row)


@router.put("/{source_id}")
async def set_spec_source_enabled(source_id: int, body: EnabledUpdate) -> dict[str, bool]:
    config_store.set_spec_source_enabled(source_id, body.enabled)
    return {"enabled": body.enabled}


@router.delete("/{source_id}", status_code=204)
async def delete_spec_source(source_id: int) -> None:
    """Deletes a real row. For a purely additive extra, this removes it
    entirely; for a materialized built-in-domain override, this reverts
    that domain back to its hardcoded default (see `sources()`) -- there is
    no separate "reset to default" endpoint because this already is one."""
    config_store.delete_spec_source(source_id)


__all__ = ["router"]
