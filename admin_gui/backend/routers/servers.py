"""Read-only introspection of the three MCP servers this repo hosts: their
live tool/resource/prompt catalogs, and (cmp-admin specifically) the full
operation catalog with pinned state for the pinned-tools toggle page.

Every catalog here is built by driving the same builder functions
(`build_admin_server`, etc.) the MCP servers themselves use at startup,
in-process, via `create_connected_server_and_client_session` -- exactly the
pattern this project's own test suite already uses. There is no already-
running server process to connect to (they're ephemeral stdio subprocesses
spawned per Claude Code session); this is genuine introspection of the same
code, not a mock.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from mcp import McpError
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from mcp_servers.cmp_admin_mcp.main import (
    all_operations_with_pinned_state,
    build_admin_server,
    operation_detail,
)
from mcp_servers.cmp_logs_mcp.server import build_server as build_logs_server
from mcp_servers.cmp_notify_mcp.server import build_server as build_notify_server
from mcp_servers.shared.config_store import set_annotation_override, set_pinned_override

router = APIRouter(prefix="/servers", tags=["servers"])

_SERVER_BUILDERS: dict[str, Callable[[], Server]] = {
    "cmp-admin": build_admin_server,
    "cmp-logs": build_logs_server,
    "cmp-notify": build_notify_server,
}

_SERVER_DESCRIPTIONS: dict[str, str] = {
    "cmp-admin": (
        "CMP admin-v2 server/network/block-storage operations, failure-pattern "
        "search, resource templates, and the assemble_log prompt."
    ),
    "cmp-logs": "Read-only Elasticsearch log search with unconditional PII masking.",
    "cmp-notify": "Outbound webhook notification for handing an incident to a human admin.",
}


class ServerSummary(BaseModel):
    id: str
    description: str


class ToolSummary(BaseModel):
    name: str
    description: str
    read_only: bool | None = None
    destructive: bool | None = None


class ResourceTemplateSummary(BaseModel):
    uri_template: str
    name: str
    description: str


class PromptSummary(BaseModel):
    name: str
    description: str | None = None


class OperationSummary(BaseModel):
    operation_id: str
    summary: str
    category: str | None = None
    risk_level: str | None = None
    read_only: bool
    destructive: bool
    pinned: bool


class PinRequest(BaseModel):
    pinned: bool


class OperationDetail(BaseModel):
    operation_id: str
    method: str
    path: str
    summary: str
    description: str
    parameters: list[dict[str, Any]]
    body_schema: dict[str, Any] | None = None
    body_required: bool
    output_schema: dict[str, Any] | None = None
    usage_note: str
    category: str | None = None
    risk_level: str | None = None
    preconditions: list[str]
    related_tools: list[str]
    read_only: bool
    destructive: bool
    idempotent: bool | None = None
    pinned: bool


class OperationDetailUpdate(BaseModel):
    usage_note: str | None = None
    category: str | None = None
    risk_level: str | None = None
    preconditions: list[str] | None = None
    related_tools: list[str] | None = None
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None


def _get_builder(server_id: str) -> Callable[[], Server]:
    builder = _SERVER_BUILDERS.get(server_id)
    if builder is None:
        raise HTTPException(status_code=404, detail=f"unknown server: {server_id}")
    return builder


def _describe_spec_source_error(exc: BaseException) -> str:
    """Both documented failure modes of a misconfigured GUI-managed spec
    source, worded for the person who just caused it, not a stack trace:

    - `ValueError` -- an operation_id collision across merged specs (see
      `openapi_bridge.merge_operations`). Easy to trigger by accident from
      the Spec Sources page, e.g. uploading the same spec twice.
    - `KeyError` -- cmp-admin's resource templates index a handful of
      built-in operations directly (`get_elastic_ip`, `get_server_compute_node`,
      ...; see `cmp_admin_mcp/resources.py`) and fail loudly if one goes
      missing. Triggered by disabling or replacing a built-in domain (e.g.
      "network") on the Spec Sources page without covering what it drops.
    """
    if isinstance(exc, KeyError):
        operation_id = exc.args[0] if exc.args else "?"
        return (
            f"missing required operation {operation_id!r} -- a built-in domain covering it was "
            "disabled or replaced with a spec that no longer has it. Re-enable that domain, or "
            "reset it to its default, on the Spec Sources page."
        )
    return str(exc)


def _build_server(server_id: str) -> Server:
    """`_get_builder(server_id)()`, with the two documented spec-source
    failure modes (see `_describe_spec_source_error`) turned into a clean
    400 instead of an unhandled 500."""
    try:
        return _get_builder(server_id)()
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=_describe_spec_source_error(exc)) from exc


def _catalog_call(fn: Callable[[], Any]) -> Any:
    """Same error translation as `_build_server`, for the cmp-admin-only
    catalog helpers (`all_operations_with_pinned_state`, `operation_detail`)
    that merge specs directly rather than through a builder function."""
    try:
        return fn()
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=_describe_spec_source_error(exc)) from exc


@router.get("", response_model=list[ServerSummary])
async def list_servers() -> list[ServerSummary]:
    return [
        ServerSummary(id=server_id, description=_SERVER_DESCRIPTIONS[server_id])
        for server_id in _SERVER_BUILDERS
    ]


@router.get("/{server_id}/tools", response_model=list[ToolSummary])
async def list_tools(server_id: str) -> list[ToolSummary]:
    """The discovery-filtered listing -- exactly what an agent sees by
    default. For cmp-admin's *full* operation catalog, see
    `/servers/cmp-admin/tools/all` below."""
    server = await run_in_threadpool(_build_server, server_id)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()
    return [
        ToolSummary(
            name=tool.name,
            description=tool.description or "",
            read_only=tool.annotations.readOnlyHint if tool.annotations else None,
            destructive=tool.annotations.destructiveHint if tool.annotations else None,
        )
        for tool in result.tools
    ]


@router.get("/cmp-admin/tools/all", response_model=list[OperationSummary])
async def list_all_admin_operations() -> list[OperationSummary]:
    entries = await run_in_threadpool(_catalog_call, all_operations_with_pinned_state)
    return [OperationSummary(**entry) for entry in entries]


@router.post("/cmp-admin/tools/{operation_id}/pin")
async def set_pin(operation_id: str, request: PinRequest) -> dict[str, bool]:
    set_pinned_override(operation_id, request.pinned)
    return {"pinned": request.pinned}


@router.get("/cmp-admin/tools/{operation_id}", response_model=OperationDetail)
async def get_operation_detail(operation_id: str) -> dict[str, Any]:
    detail = await run_in_threadpool(_catalog_call, lambda: operation_detail(operation_id))
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown operation: {operation_id}")
    return detail


@router.put("/cmp-admin/tools/{operation_id}", response_model=OperationDetail)
async def update_operation_detail(operation_id: str, update: OperationDetailUpdate) -> dict[str, Any]:
    existing = await run_in_threadpool(_catalog_call, lambda: operation_detail(operation_id))
    if existing is None:
        raise HTTPException(status_code=404, detail=f"unknown operation: {operation_id}")
    fields = update.model_dump(exclude_unset=True)
    if "category" in fields:
        fields["tool_category"] = fields.pop("category")
    if fields:
        set_annotation_override(operation_id, fields)
    detail = await run_in_threadpool(_catalog_call, lambda: operation_detail(operation_id))
    assert detail is not None
    return detail


@router.get("/{server_id}/resources", response_model=list[ResourceTemplateSummary])
async def list_resource_templates(server_id: str) -> list[ResourceTemplateSummary]:
    server = await run_in_threadpool(_build_server, server_id)
    async with create_connected_server_and_client_session(server) as session:
        try:
            result = await session.list_resource_templates()
        except McpError:
            return []
    return [
        ResourceTemplateSummary(
            uri_template=template.uriTemplate,
            name=template.name,
            description=template.description or "",
        )
        for template in result.resourceTemplates
    ]


@router.get("/{server_id}/prompts", response_model=list[PromptSummary])
async def list_prompts(server_id: str) -> list[PromptSummary]:
    server = await run_in_threadpool(_build_server, server_id)
    async with create_connected_server_and_client_session(server) as session:
        try:
            result = await session.list_prompts()
        except McpError:
            return []
    return [
        PromptSummary(name=prompt.name, description=prompt.description) for prompt in result.prompts
    ]


__all__ = ["router"]
