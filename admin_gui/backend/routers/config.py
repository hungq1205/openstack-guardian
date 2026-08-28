"""CRUD for the three connections cmp-mcp's servers talk to (CMP admin-v2
API, Elasticsearch, the notify webhook), plus a real "test connection"
check for each -- using whatever fields are in the request body merged over
what's already saved, so a value can be tested before it's saved.

Secret-shaped fields (PAT, passwords, API keys, the webhook URL) come back
masked from GET -- an update only needs to include the fields it's actually
changing, unmodified secrets don't need to be resent.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from mcp_servers.cmp_admin_mcp.main import sources as admin_sources
from mcp_servers.cmp_logs_mcp.client import ElasticsearchLogsClient
from mcp_servers.cmp_notify_mcp.client import NotifyClient
from mcp_servers.openapi_bridge import (
    CmpApiClient,
    OperationSpec,
    SpecSource,
    annotations_for,
    load_annotations,
    merge_operations,
)
from mcp_servers.shared.config_store import get_connection_config, set_connection_config

router = APIRouter(prefix="/config", tags=["config"])

_CONNECTION_KEYS: dict[str, str] = {
    "cmp-admin-v2": "CMP_ADMIN_V2",
    "elasticsearch": "CMP_LOGS_ES",
    "notify": "CMP_NOTIFY",
}

_SECRET_FIELDS: dict[str, set[str]] = {
    "cmp-admin-v2": {"pat", "password"},
    "elasticsearch": {"api_key", "password"},
    "notify": {"webhook_url"},
}


def _mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _masked_view(connection: str, fields: dict[str, Any]) -> dict[str, Any]:
    secret_names = _SECRET_FIELDS.get(connection, set())
    return {
        key: (_mask_secret(value) if key in secret_names and value else value)
        for key, value in fields.items()
    }


def _resolve_key(connection: str) -> str:
    key = _CONNECTION_KEYS.get(connection)
    if key is None:
        raise HTTPException(status_code=404, detail=f"unknown connection: {connection}")
    return key


@router.get("/{connection}")
async def get_connection(connection: str) -> dict[str, Any]:
    key = _resolve_key(connection)
    return _masked_view(connection, get_connection_config(key))


@router.put("/{connection}")
async def update_connection(
    connection: str, update: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    key = _resolve_key(connection)
    merged = {**get_connection_config(key), **update}
    set_connection_config(key, merged)
    return _masked_view(connection, merged)


def _pick_probe_operation(
    sources: list[SpecSource], operations: dict[str, OperationSpec]
) -> OperationSpec | None:
    """A cheap, safe operation to call for the connection test -- prefers
    `list_compute_nodes` (the operation this has always probed with) but
    doesn't assume it exists: an administrator can disable/replace the
    built-in "server" spec domain from the Spec Sources page, in which case
    that operation id may not be in `operations` at all. Falls back to any
    read-only GET with no required parameters, so the probe still works
    against whatever domains are actually active; returns None only if
    nothing fits (nothing to call, not a real failure of the connection
    itself)."""
    if (preferred := operations.get("list_compute_nodes")) is not None:
        return preferred
    annotations: dict[str, Any] = {}
    for source in sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    for op in operations.values():
        if op.method != "GET" or any(param.required for param in op.parameters):
            continue
        if annotations_for(op, annotations.get(op.operation_id)).readOnlyHint:
            return op
    return None


@router.post("/cmp-admin-v2/test-connection")
async def test_cmp_admin_connection(
    update: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """A real, cheap, read-only GET -- there's no dedicated health/ping
    operation in the CMP admin-v2 API, so this borrows a real list
    operation instead. See `_pick_probe_operation` for how it's chosen."""
    fields = {**get_connection_config("CMP_ADMIN_V2"), **(update or {})}
    base_url = fields.get("base_url") or ""
    if not base_url:
        return {"reachable": False, "message": "no base URL configured"}
    client = CmpApiClient.from_credentials(
        base_url,
        pat=fields.get("pat"),
        username=fields.get("username"),
        password=fields.get("password"),
    )
    sources = admin_sources()
    operations = merge_operations(sources)
    op = _pick_probe_operation(sources, operations)
    if op is None:
        return {
            "reachable": False,
            "message": "no read-only, parameterless operation available to test with",
        }
    args = {"page_size": 1} if any(param.name == "page_size" for param in op.parameters) else {}
    result = client.call(op, args)
    if "error" in result:
        return {"reachable": False, "message": result.get("message", result["error"])}
    return {"reachable": True, "status_code": result.get("status_code")}


@router.post("/elasticsearch/test-connection")
async def test_elasticsearch_connection(
    update: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    fields = {**get_connection_config("CMP_LOGS_ES"), **(update or {})}
    return ElasticsearchLogsClient.from_fields(fields).check_connection()


@router.post("/notify/test-connection")
async def test_notify_connection(
    update: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    fields = {**get_connection_config("CMP_NOTIFY"), **(update or {})}
    return NotifyClient.from_fields(fields).send_test()


__all__ = ["router"]
