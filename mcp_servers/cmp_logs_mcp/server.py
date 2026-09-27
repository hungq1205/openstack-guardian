"""Hand-rolled MCP server for CMP/core Elasticsearch log search.

Not built on `mcp_servers.openapi_bridge`'s generic OpenAPI-to-MCP machinery
-- Elasticsearch's search API isn't described by an OpenAPI spec here, and a
handful of hardcoded operations doesn't need that generality. Still reuses
the OpenAPI-agnostic half of that module -- `ToolAnnotation`,
`load_annotations`, `apply_annotation_override` all key off a plain tool
name, none require an `OperationSpec` -- to give these 2 tools the same
GUI-editable `hidden`/`section`/`tool_category` curation cmp-admin's
operations have, via `mcp_servers/annotations/cmp_logs.json`. Deliberately
does *not* reuse `annotations_for`/`description_for`: both require a real
`OperationSpec` (an actual method+path), and inventing a fake one just to
satisfy that signature would be worse than the small amount of duplication
avoided -- these 2 tools already carry hand-written, comprehensive
descriptions and a correct hardcoded `ToolAnnotations`, unlike an
OpenAPI-derived tool whose spec-only text genuinely needs supplementing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from guardian_platform.config_store import get_tool_annotation_overrides
from mcp import types
from mcp.server.lowlevel import Server

from mcp_servers.cmp_logs_mcp.client import ElasticsearchLogsClient
from mcp_servers.openapi_bridge import (
    ToolAnnotation,
    apply_annotation_override,
    load_annotations,
)

_SEARCH_LOGS = "search_logs"
_FOLLOW_REQUEST_ID = "follow_request_id"

_ANNOTATIONS_PATH = Path(__file__).resolve().parent.parent / "annotations" / "cmp_logs.json"

_READ_ONLY_ANNOTATIONS = types.ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)

_SEARCH_LOGS_DESCRIPTION = (
    "General-purpose search across CMP/core Elasticsearch logs, ranked oldest-first. An "
    "optional free-text `query` (matched as a phrase against the full log message/raw line, "
    "not a specific field) combines freely with any number of structured filters and a time "
    "range. At least one of query/level/exclude_level/logger/request_id/method/status/"
    "path_prefix/hostname/since/until is required -- an empty call is refused to avoid "
    "scanning the whole index.\n\n"
    "Common calls:\n"
    "- A resource id, exception class, or error fragment anywhere in the text: "
    "{query: \"98fc04db-66cd-4b62-a69e-a1c6f81a0aac\"}\n"
    "- Only that text within a time window: "
    "{query: \"cinderclient.exceptions.NotFound\", since: \"2026-08-23T23:00:00\", "
    "until: \"2026-08-23T23:30:00\"}\n"
    "- Every ERROR on one host in a window, no text needed: "
    "{level: \"ERROR\", hostname: \"iaas-2\", since: ..., until: ...}\n"
    "- Everything but healthy INFO traffic in a window, with known noise (a repeated "
    "deprecation warning, a cache notice, uWSGI chatter) excluded: "
    "{exclude_level: \"INFO\", exclude_known_noise: true, since: ..., until: ...}\n"
    "- Other 404s on a given API path: {path_prefix: \"/api/v1/vbs\", status: 404}\n"
    "- Narrow a text match further by level and host in one call: "
    "{query: \"NotFound\", level: \"ERROR\", hostname: \"iaas-2\"}\n\n"
    "For a single already-known gateway request-id, follow_request_id is a simpler shortcut "
    "than {request_id: ...} here. Every returned field has already been scanned for emails/"
    "IPs/phone numbers and masked -- this is the only way to read these logs, there is no "
    "unmasked path."
)
_SEARCH_LOGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-text phrase to search for -- e.g. a resource id/UUID, an exception "
                "class name, or a distinctive error fragment. Matched against the full log "
                "message text, not a specific field."
            ),
        },
        "level": {"type": "string", "description": "Exact log level, e.g. 'ERROR', 'WARNING'."},
        "exclude_level": {
            "type": "string",
            "description": "Exclude one exact log level, e.g. 'INFO' to surface only anomalies.",
        },
        "logger": {
            "type": "string",
            "description": "Exact logger name, e.g. 'log.response' for access-log entries only.",
        },
        "request_id": {"type": "string", "description": "Exact gateway correlation id."},
        "method": {"type": "string", "description": "e.g. 'GET' or 'DELETE'."},
        "status": {"type": "integer", "description": "Exact HTTP status code, e.g. 404."},
        "path_prefix": {"type": "string", "description": "e.g. '/api/v1/vbs'."},
        "hostname": {"type": "string", "description": "e.g. 'iaas-1'."},
        "since": {"type": "string", "description": "ISO-8601 inclusive lower bound."},
        "until": {"type": "string", "description": "ISO-8601 inclusive upper bound."},
        "exclude_known_noise": {
            "type": "boolean",
            "description": (
                "Exclude the neutronclient deprecation warning, matplotlib cache notice, "
                "and uWSGI worker-lifecycle chatter. Default false."
            ),
        },
        "max_results": {"type": "integer", "description": "Default 100, capped at 1000."},
    },
    "required": [],
}

_FOLLOW_REQUEST_ID_DESCRIPTION = (
    "Returns every structured log entry (INFO/WARNING/ERROR) tagged with this gateway "
    "request-id, oldest first -- reconstructs one incident's cascade across levels. Raw "
    "stderr/uWSGI access lines and Python tracebacks are not tagged with a request-id at "
    "the source and won't appear here even for the same incident -- use search_logs with "
    "exclude_level='INFO' over the same time window to see those. Every returned field has "
    "already been masked."
)
_FOLLOW_REQUEST_ID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_id": {
            "type": "string",
            "description": (
                "The gateway correlation id (the bracketed id in [...] immediately after "
                "the timestamp in a structured log line) to follow."
            ),
        },
        "max_results": {"type": "integer", "description": "Default 200, capped at 1000."},
    },
    "required": ["request_id"],
}


_TOOL_DEFS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (_SEARCH_LOGS, _SEARCH_LOGS_DESCRIPTION, _SEARCH_LOGS_SCHEMA),
    (_FOLLOW_REQUEST_ID, _FOLLOW_REQUEST_ID_DESCRIPTION, _FOLLOW_REQUEST_ID_SCHEMA),
)


def _curated_annotations() -> dict[str, ToolAnnotation | None]:
    """Both tools' effective curated annotation (shipped JSON + any admin
    GUI override applied) -- shared by `build_server` (the live server) and
    `all_tools_with_visibility` (the admin GUI's catalog listing), so both
    always agree."""
    base_annotations = load_annotations(_ANNOTATIONS_PATH)
    overrides = get_tool_annotation_overrides("cmp-logs")
    return {
        name: apply_annotation_override(base_annotations.get(name), overrides.get(name))
        for name, _description, _schema in _TOOL_DEFS
    }


def _tool_annotations(name: str, curated: dict[str, ToolAnnotation | None]) -> types.ToolAnnotations:
    """Both tools are read-only by nature -- `_READ_ONLY_ANNOTATIONS` is the
    base every curated override, if any, layers on top of, same pattern as
    `openapi_bridge.annotations_for`'s method-based base."""
    hints = _READ_ONLY_ANNOTATIONS.model_dump(exclude_none=True)
    entry = curated[name]
    if entry is not None:
        if entry.read_only is not None:
            hints["readOnlyHint"] = entry.read_only
        if entry.destructive is not None:
            hints["destructiveHint"] = entry.destructive
        if entry.idempotent is not None:
            hints["idempotentHint"] = entry.idempotent
    return types.ToolAnnotations.model_validate(hints)


def build_server() -> Server:
    """2026-09-26: piped through `guardian-admin` as a proxied external
    connection now (see the top-level workspace CLAUDE.md's "proxy-gateway"
    section), same as `cmp_admin_mcp`'s own discovery-layer server -- raw,
    ungated dispatch; logging/approval-gating and enable/disable both moved
    to that single proxy layer. `_list_tools` no longer filters by the old
    curated `hidden` field either -- always both tools, unfiltered."""
    client = ElasticsearchLogsClient.from_env()
    server: Server = Server("cmp-logs")

    curated = _curated_annotations()

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=description,
                inputSchema=schema,
                annotations=_tool_annotations(name, curated),
            )
            for name, description, schema in _TOOL_DEFS
        ]

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == _SEARCH_LOGS:
            status = arguments.get("status")
            return client.search(
                query=arguments.get("query"),
                level=arguments.get("level"),
                exclude_level=arguments.get("exclude_level"),
                logger=arguments.get("logger"),
                request_id=arguments.get("request_id"),
                method=arguments.get("method"),
                status=int(status) if status is not None else None,
                path_prefix=arguments.get("path_prefix"),
                hostname=arguments.get("hostname"),
                since=arguments.get("since"),
                until=arguments.get("until"),
                exclude_known_noise=bool(arguments.get("exclude_known_noise", False)),
                max_results=int(arguments.get("max_results", 100)),
            )
        if tool_name == _FOLLOW_REQUEST_ID:
            return client.follow_request_id(
                request_id=arguments["request_id"],
                max_results=int(arguments.get("max_results", 200)),
            )
        return {"error": "unknown_tool", "tool": tool_name}

    return server


__all__ = ["build_server"]
