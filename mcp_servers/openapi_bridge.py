"""Generic OpenAPI 3.1 -> MCP server bridge.

Parses any OpenAPI spec into one MCP tool per operationId and makes real
HTTP calls against the described API, authenticated per the spec's own
securitySchemes (HTTP Basic or a bearer-style PAT token). Adding a fourth
CMP API later -- or any other OpenAPI-described service -- is pointing a new
entrypoint at its spec file with its own env-var prefix; this module never
changes.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import anyio
import httpx
from guardian_platform.config_store import get_config_value
from guardian_platform.telemetry import instrument_dispatch
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_DEFAULT_TIMEOUT_SECONDS = 30

# HTTP method is the only signal OpenAPI gives us for free about how an agent
# should treat a call -- readOnlyHint/idempotentHint/destructiveHint are MCP's
# own protocol fields, with no OpenAPI equivalent at all. A method-based guess
# is a floor, not a substitute for real domain knowledge: a curated
# ToolAnnotation (below) overrides any of these when one exists.
_METHOD_HINTS: dict[str, dict[str, bool]] = {
    "GET": {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False},
    "PUT": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False},
    "PATCH": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": False},
    "DELETE": {"readOnlyHint": False, "idempotentHint": True, "destructiveHint": True},
    "POST": {"readOnlyHint": False, "idempotentHint": False, "destructiveHint": False},
}


@dataclass(frozen=True)
class ParamSpec:
    """One path or query parameter for an operation."""

    name: str
    location: str
    required: bool
    schema: dict[str, Any]


@dataclass(frozen=True)
class OperationSpec:
    """One OpenAPI operation (path + method), fully self-contained.

    `body_schema`, `output_schema`, and every parameter's `schema` have
    already had every `$ref` resolved against the spec's
    `components.schemas` -- nothing about an `OperationSpec` depends on the
    original document once it's built.
    """

    operation_id: str
    method: str
    path: str
    summary: str
    description: str
    parameters: tuple[ParamSpec, ...]
    body_schema: dict[str, Any] | None
    body_required: bool
    output_schema: dict[str, Any] | None = None


def action_for_operation(op: OperationSpec) -> str:
    """The human-readable "what actually runs" for an operation-backed call
    -- an HTTP method + unsubstituted path, e.g. `GET /admin-api/servers/{id}/`
    -- for the events log's Action column (see `telemetry.instrument_dispatch`)."""
    return f"{op.method.upper()} {op.path}"


@dataclass(frozen=True)
class ToolAnnotation:
    """Curated, agent-facing guidance for one operation that no OpenAPI field carries.

    Sparse by design -- only operations someone has actually looked at need
    an entry. Everything else falls back to the method-based hint and the
    raw spec text. `risk_level`/`tool_category` are this project's own
    vocabulary (see the design plan); `read_only`/`destructive`/`idempotent`
    override the mechanical HTTP-method guess when set.
    """

    usage_note: str = ""
    risk_level: str | None = None
    tool_category: str | None = None
    preconditions: tuple[str, ...] = ()
    related_tools: tuple[str, ...] = ()
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    hidden: bool = False
    """Opt-out visibility: `False` (the default) means always listed in a
    discovery-enabled server's `list_tools()` (see `build_multi_spec_server`)
    -- a rarely-needed tool an admin explicitly marks `hidden=True` is still
    callable via `call_tool`, just found through `search_tools`/
    `get_tool_schema` first instead of showing up unprompted. Replaces the
    old opt-in `pinned` field (inverse polarity: `pinned=False` used to mean
    hidden-by-default; `hidden=False` now means visible-by-default) -- see
    `guardian_platform/db.py`'s `_migrate_legacy_overrides` for how
    existing `pinned` data carries over."""
    section: str | None = None
    """Purely a display/grouping label for the admin GUI's Catalog page
    (e.g. "system", "logs", "cmp-operations") -- a different axis from
    `tool_category`, which still drives approval gating. Does not affect
    which MCP server a tool is actually reachable through."""


def load_annotations(path: Path) -> dict[str, ToolAnnotation]:
    """Load a sparse operationId -> ToolAnnotation overlay. Missing file means no overlay yet."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        operation_id: ToolAnnotation(
            usage_note=entry.get("usage_note", ""),
            risk_level=entry.get("risk_level"),
            tool_category=entry.get("tool_category"),
            preconditions=tuple(entry.get("preconditions", ())),
            related_tools=tuple(entry.get("related_tools", ())),
            read_only=entry.get("read_only"),
            destructive=entry.get("destructive"),
            idempotent=entry.get("idempotent"),
            hidden=bool(entry.get("hidden", False)),
            section=entry.get("section"),
        )
        for operation_id, entry in raw.items()
    }


def annotations_for(op: OperationSpec, curated: ToolAnnotation | None) -> types.ToolAnnotations:
    """Public so any other consumer of this spec can reuse the exact same hints this
    server exposes, rather than re-deriving them."""
    hints = dict(_METHOD_HINTS.get(op.method, {}))
    if curated is not None:
        if curated.read_only is not None:
            hints["readOnlyHint"] = curated.read_only
        if curated.destructive is not None:
            hints["destructiveHint"] = curated.destructive
        if curated.idempotent is not None:
            hints["idempotentHint"] = curated.idempotent
    return types.ToolAnnotations.model_validate(hints)


_PAGINATION_NOTE = (
    "Supports pagination via page_size (max 100) and page_number (max 10000); "
    "check the response's count/next/previous fields to see if more results remain."
)


def first_sentence(text: str) -> str:
    """First sentence of a hand-written, long-form tool description, for a
    catalog list view's `summary` column -- for a hand-built tool (no
    backing `OperationSpec`, so no separate short `summary` field the way an
    OpenAPI operation has one). Safe as a plain first-period split only for
    prose written without an earlier abbreviation/decimal period in the
    first sentence; not a general-purpose sentence splitter. Shared by
    `cmp_admin_mcp.main` (its 5 `ExtraTool`s) and `cmp_logs_mcp.server`
    (its 2 hand-built tools)."""
    first, sep, _ = text.partition(". ")
    return first + "." if sep else first


def description_for(op: OperationSpec, curated: ToolAnnotation | None) -> str:
    """Public -- same reasoning as `annotations_for`."""
    base_text = op.description or op.summary
    parts = [f"{op.method} {op.path} -- {base_text}"]
    if curated is not None:
        if curated.usage_note:
            parts.append(f"Usage: {curated.usage_note}")
        if curated.tool_category:
            parts.append(f"Category: {curated.tool_category}")
        if curated.preconditions:
            parts.append(f"Preconditions: {'; '.join(curated.preconditions)}")
        if curated.related_tools:
            parts.append(f"Related tools: {', '.join(curated.related_tools)}")
    param_names = {param.name for param in op.parameters}
    if {"page_size", "page_number"} <= param_names:
        parts.append(_PAGINATION_NOTE)
    return " | ".join(parts)


def _resolve_schema(
    schema: Any,
    components: dict[str, Any],
    seen: frozenset[str] = frozenset(),
) -> Any:
    """Recursively inline `$ref` pointers into `components/schemas`.

    A cycle (a schema that refers back to one already being resolved) falls
    back to a bare ``{"type": "object"}`` rather than recursing forever --
    none of the CMP admin schemas are self-referential today, but a future
    spec might be.
    """
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.removeprefix("#/components/schemas/")
            if name in seen:
                return {"type": "object"}
            target = components.get(name, {})
            return _resolve_schema(target, components, seen | {name})
        all_of = schema.get("allOf")
        if isinstance(all_of, list) and len(all_of) == 1 and isinstance(all_of[0], dict):
            # drf-spectacular's `{"allOf": [{"$ref": ...}], "nullable": true, ...}` idiom
            # for a nullable ref -- flatten it so the referenced schema's own "type"
            # ends up alongside these sibling keys instead of behind an opaque allOf.
            resolved_target = _resolve_schema(all_of[0], components, seen)
            if isinstance(resolved_target, dict):
                overrides = {
                    key: _resolve_schema(value, components, seen)
                    for key, value in schema.items()
                    if key != "allOf"
                }
                merged = {**resolved_target, **overrides}
                if "nullable" in merged and "type" not in merged:
                    merged.pop("nullable")
                return merged
        resolved = {key: _resolve_schema(value, components, seen) for key, value in schema.items()}
        if "nullable" in resolved and "type" not in resolved:
            # e.g. `{"nullable": true, "oneOf": [...enum refs including a NullEnum...]}`
            # -- no top-level "type" to pair it with, and the null case is already
            # covered by the NullEnum branch, so the flag is redundant here.
            resolved.pop("nullable")
        return resolved
    if isinstance(schema, list):
        return [_resolve_schema(item, components, seen) for item in schema]
    return schema


def _response_schema(op: dict[str, Any], components: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the lowest declared 2xx response's JSON body schema, if any.

    Never assumes a specific status code (`delete_aggregate` returns 201
    with no content where every other DELETE in these specs returns 204;
    `list_server_types`'s paged schema lacks the `next`/`previous` fields
    every other paged schema has) -- whatever the spec actually declares for
    the lowest 2xx code present is resolved as-is, and a code with no
    `content` at all (the common case for mutating actions) yields `None`.
    """
    responses = op.get("responses", {})
    codes = sorted(code for code in responses if code.startswith("2") and code.isdigit())
    if not codes:
        return None
    content = responses[codes[0]].get("content", {}).get("application/json", {})
    schema = content.get("schema")
    if schema is None:
        return None
    return _resolve_schema(schema, components)


def load_operations(spec_path: Path) -> dict[str, OperationSpec]:
    """Parse an OpenAPI 3.1 spec into one `OperationSpec` per operationId."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    components = spec.get("components", {}).get("schemas", {})
    operations: dict[str, OperationSpec] = {}
    for path, path_item in spec.get("paths", {}).items():
        for method, op in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation_id = op.get("operationId")
            if not operation_id:
                continue
            parameters = tuple(
                ParamSpec(
                    name=param["name"],
                    location=param["in"],
                    required=bool(param.get("required", False)),
                    schema=_resolve_schema(param.get("schema", {}), components),
                )
                for param in op.get("parameters", [])
                if param.get("in") in {"path", "query"}
            )
            body_schema: dict[str, Any] | None = None
            body_required = False
            request_body = op.get("requestBody")
            if request_body:
                content = request_body.get("content", {}).get("application/json", {})
                if "schema" in content:
                    body_schema = _resolve_schema(content["schema"], components)
                    body_required = bool(request_body.get("required", False))
            operations[operation_id] = OperationSpec(
                operation_id=operation_id,
                method=method.upper(),
                path=path,
                summary=op.get("summary", operation_id),
                description=op.get("description") or op.get("summary", ""),
                parameters=parameters,
                body_schema=body_schema,
                body_required=body_required,
                output_schema=_response_schema(op, components),
            )
    return operations


def build_input_schema(op: OperationSpec) -> dict[str, Any]:
    """Build one flat JSON Schema object combining path/query params and the request body.

    Flattened rather than nested under a "body" key when the body is itself
    an object -- one flat set of fields is simpler for a model to fill in
    correctly than a nested structure mirroring the wire format.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in op.parameters:
        properties[param.name] = param.schema
        if param.required:
            required.append(param.name)
    if op.body_schema is not None:
        body_properties = op.body_schema.get("properties")
        if body_properties:
            properties.update(body_properties)
            required.extend(op.body_schema.get("required", []))
        else:
            properties["body"] = op.body_schema
            if op.body_required:
                required.append("body")
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(set(required)),
    }


def _wrap_output_schema(resolved: dict[str, Any]) -> dict[str, Any]:
    """Wrap a resolved response schema to match what `CmpApiClient.call` actually
    returns -- `{"status_code": ..., "data": <resolved-or-error-body>}` on any real HTTP
    response, or `{"error": ..., "message": ...}` only for a connection-level failure (never
    configured, request failed -- see `CmpApiClient.call`'s own `except httpx.HTTPError`
    branch) -- never the bare resolved schema at the top level. Declaring `outputSchema` as
    the bare schema would fail the SDK's own output validation on every real call, since the
    real `structuredContent` is always one of these two envelopes.

    `data` only has to match `resolved` when `status_code` is actually 2xx (an `if`/`then`, not
    a bare `anyOf` -- see below for why that distinction matters): `CmpApiClient.call` wraps
    every HTTP response the same way regardless of status code -- a real 404/400/403/401 (every
    `server_v1` operation declares at least one, all `ErrorResponse`-shaped, e.g.
    `{"detail": "Not found."}`) comes back in exactly the same envelope as a 200, just with
    `data` holding the error body instead of the success one. Requiring `resolved` unconditionally
    would (and did, live, 2026-09-27, ticket #98's `admin_api_servers_retrieve` -- a completely
    valid 404 hitting a never-created server, the KB's own designed starting state) reject any
    non-2xx response outright, since an `ErrorResponse` obviously doesn't validate against e.g.
    `ServerAdminDetail`.

    This is deliberately an `if`/`then` gated on `status_code`, not `data: {"anyOf": [resolved, {}]}`
    unconditionally -- the latter was tried first and rejected: `{}` (JSON Schema's "matches
    anything") on one `anyOf` branch makes *every* `data` value satisfy the schema regardless of
    status code, silently accepting a broken *200* response too (missing required fields, wrong
    types) -- exactly the class of real mock bugs a strict schema had just caught earlier the same
    day (ticket #94/#95, missing `flavor`/`key_pair`/`enable_ipv4`/... on legitimate 200s). The
    goal is "never spuriously reject a real error response," not "stop verifying success
    responses" -- a 2xx still has to be shaped exactly like `resolved`; only a non-2xx status_code
    gets a free pass on `data`'s shape, since that's genuinely CMP's own error body, not something
    to police here.
    """
    return {
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "properties": {"status_code": {"type": "integer"}, "data": {}},
                "required": ["status_code", "data"],
                "if": {"properties": {"status_code": {"minimum": 200, "maximum": 299}}},
                "then": {"properties": {"data": resolved}},
            },
            {
                "type": "object",
                "properties": {"error": {"type": "string"}, "message": {"type": "string"}},
                "required": ["error"],
            },
        ]
    }


def _tool_for(op: OperationSpec, curated: ToolAnnotation | None) -> types.Tool:
    """Build the `types.Tool` for one operation -- shared by every server builder
    in this module so outputSchema wrapping and annotation/description logic
    lives in exactly one place."""
    return types.Tool(
        name=op.operation_id,
        description=description_for(op, curated),
        inputSchema=build_input_schema(op),
        outputSchema=_wrap_output_schema(op.output_schema)
        if op.output_schema is not None
        else None,
        annotations=annotations_for(op, curated),
    )


@dataclass
class CmpApiClient:
    """Real HTTP client for one CMP admin API, configured from environment variables.

    `{env_prefix}_BASE_URL` plus either `{env_prefix}_PAT` (sent as
    ``Authorization: token <pat>``, matching the spec's PATAuth scheme) or
    `{env_prefix}_USERNAME`/`{env_prefix}_PASSWORD` (HTTP Basic, matching
    BasicAuth). PAT takes precedence when both are set.
    """

    base_url: str
    auth_header: dict[str, str]

    @classmethod
    def from_credentials(
        cls,
        base_url: str,
        *,
        pat: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> CmpApiClient:
        """Build directly from already-collected credentials -- e.g. the
        admin GUI's connection-test endpoint, checking a not-yet-saved form
        value. `from_env` is just this plus env-var/config-store lookup."""
        auth_header: dict[str, str] = {}
        if pat:
            auth_header["Authorization"] = f"token {pat}"
        elif username and password:
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth_header["Authorization"] = f"Basic {credentials}"
        return cls(base_url=base_url.rstrip("/"), auth_header=auth_header)

    @classmethod
    def from_env(cls, env_prefix: str) -> CmpApiClient:
        """Env var wins if set; otherwise falls back to the admin GUI's config
        store (see `shared.config_store`), then to an empty default -- a
        server run without ever touching the GUI is unaffected."""
        base_url = os.environ.get(f"{env_prefix}_BASE_URL") or get_config_value(
            env_prefix, "base_url"
        )
        pat = os.environ.get(f"{env_prefix}_PAT") or get_config_value(env_prefix, "pat")
        username = os.environ.get(f"{env_prefix}_USERNAME") or get_config_value(
            env_prefix, "username"
        )
        password = os.environ.get(f"{env_prefix}_PASSWORD") or get_config_value(
            env_prefix, "password"
        )
        return cls.from_credentials(base_url or "", pat=pat, username=username, password=password)

    def call(
        self,
        op: OperationSpec,
        args: dict[str, Any],
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """Build and execute the real HTTP request for one operation.

        Never raises -- a request failure, a missing base URL, or a non-JSON
        response all come back as a structured `{"error": ...}` dict, the
        same convention used by every other tool in this codebase.
        """
        if not self.base_url:
            return {
                "error": "not_configured",
                "message": f"{op.operation_id}: no base URL configured",
            }
        path = op.path
        query: dict[str, Any] = {}
        body = dict(args)
        for param in op.parameters:
            if param.name not in body:
                continue
            value = body.pop(param.name)
            if param.location == "path":
                path = path.replace("{" + param.name + "}", str(value))
            else:
                query[param.name] = value
        url = f"{self.base_url}{path}"
        owns_client = client is None
        http_client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        try:
            response = http_client.request(
                op.method,
                url,
                params=query or None,
                json=body or None,
                headers={"Content-Type": "application/json", **self.auth_header},
            )
            try:
                data: Any = response.json()
            except ValueError:
                data = {"raw": response.text}
            return {"status_code": response.status_code, "data": data}
        except httpx.HTTPError as exc:
            return {"error": "request_failed", "message": str(exc)}
        finally:
            if owns_client:
                http_client.close()


def _register_tool_handlers(
    server: Server,
    operations: dict[str, OperationSpec],
    annotations: dict[str, ToolAnnotation],
    client: CmpApiClient,
) -> None:
    """Wire `list_tools`/`call_tool` for a fixed operation set -- shared by
    every server builder in this module so there is exactly one dispatch
    implementation, not one per builder."""

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [_tool_for(op, annotations.get(op.operation_id)) for op in operations.values()]

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def _dispatch() -> dict[str, Any]:
            op = operations.get(tool_name)
            if op is None:
                return {"error": "unknown_tool", "tool": tool_name}
            return client.call(op, arguments)

        op = operations.get(tool_name)
        return await instrument_dispatch(
            server=server.name,
            kind="tool",
            name=tool_name,
            arguments=arguments,
            source=f"{server.name}.tool.{tool_name}",
            dispatch=_dispatch,
            action=action_for_operation(op) if op is not None else None,
        )


_SEARCH_TOOLS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-text keywords matched against operation id, summary, description, "
                "usage note, category, and related tools (e.g. 'resize server'). Omit or "
                "leave empty to match everything, typically combined with a filter below."
            ),
        },
        "category": {
            "type": "string",
            "description": "Restrict to one tool_category, e.g. read/action/config/admin.",
        },
        "read_only": {
            "type": "boolean",
            "description": "Restrict to tools whose readOnlyHint matches this value.",
        },
        "limit": {
            "type": "integer",
            "description": "Max results to return. Default 20, capped at 50.",
            "default": 20,
        },
    },
    "required": [],
}

_SEARCH_TOOLS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "total_matches": {"type": "integer"},
        "returned": {"type": "integer"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "category": {"type": ["string", "null"]},
                    "read_only": {"type": "boolean"},
                },
                "required": ["operation_id", "summary", "read_only"],
            },
        },
    },
    "required": ["total_matches", "returned", "results"],
}

_GET_TOOL_SCHEMA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation_id": {
            "type": "string",
            "description": "Exact tool name to fetch, e.g. one returned by search_tools.",
        },
    },
    "required": ["operation_id"],
}

_GET_TOOL_SCHEMA_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": ["object", "null"]},
                "annotations": {"type": ["object", "null"]},
            },
            "required": ["name", "description", "inputSchema"],
        },
        {
            "type": "object",
            "properties": {"error": {"type": "string"}, "tool": {}},
            "required": ["error"],
        },
    ]
}


def _search_tools_meta_tool() -> types.Tool:
    return types.Tool(
        name="search_tools",
        description=(
            "Search every operation available on this server, including the rarely-used "
            "ones list_tools leaves out by default -- every operation here is reachable by "
            "exact name regardless. Use this first to find the right operation_id, then "
            "get_tool_schema for its exact input shape before calling it."
        ),
        inputSchema=_SEARCH_TOOLS_INPUT_SCHEMA,
        outputSchema=_SEARCH_TOOLS_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(
            readOnlyHint=True, idempotentHint=True, destructiveHint=False
        ),
    )


def _get_tool_schema_meta_tool() -> types.Tool:
    return types.Tool(
        name="get_tool_schema",
        description=(
            "Get the full tool definition -- description, inputSchema, outputSchema, and "
            "annotations -- for one operation_id by exact name."
        ),
        inputSchema=_GET_TOOL_SCHEMA_INPUT_SCHEMA,
        outputSchema=_GET_TOOL_SCHEMA_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(
            readOnlyHint=True, idempotentHint=True, destructiveHint=False
        ),
    )


def _search_score(op: OperationSpec, curated: ToolAnnotation | None, tokens: list[str]) -> int:
    """Count token occurrences across every field a curator or spec author actually
    wrote text into -- no ranking model, just a deterministic floor that's good
    enough to shrink 100+ operations down to a handful of candidates."""
    haystack_parts = [op.operation_id, op.summary, op.description]
    if curated is not None:
        haystack_parts.append(curated.usage_note)
        if curated.tool_category:
            haystack_parts.append(curated.tool_category)
        haystack_parts.extend(curated.related_tools)
    haystack = " ".join(haystack_parts).lower()
    return sum(haystack.count(token) for token in tokens)


def _run_search_tools(
    operations: dict[str, OperationSpec],
    annotations: dict[str, ToolAnnotation],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    category = arguments.get("category")
    read_only = arguments.get("read_only")
    try:
        limit = int(arguments.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))
    tokens = [token for token in query.lower().split() if token]

    scored: list[tuple[int, str]] = []
    for operation_id, op in operations.items():
        curated = annotations.get(operation_id)
        if category is not None and (curated is None or curated.tool_category != category):
            continue
        if read_only is not None and annotations_for(op, curated).readOnlyHint != read_only:
            continue
        score = _search_score(op, curated, tokens) if tokens else 0
        if tokens and score == 0:
            continue
        scored.append((score, operation_id))

    scored.sort(key=lambda item: (-item[0], item[1]))
    results = []
    for _score, operation_id in scored[:limit]:
        op = operations[operation_id]
        curated = annotations.get(operation_id)
        hints = annotations_for(op, curated)
        results.append(
            {
                "operation_id": operation_id,
                "summary": op.summary,
                "category": curated.tool_category if curated else None,
                "read_only": bool(hints.readOnlyHint),
            }
        )
    return {"total_matches": len(scored), "returned": len(results), "results": results}


def _run_get_tool_schema(
    operations: dict[str, OperationSpec],
    annotations: dict[str, ToolAnnotation],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    operation_id = arguments.get("operation_id")
    op = operations.get(operation_id) if isinstance(operation_id, str) else None
    if op is None:
        return {"error": "unknown_tool", "tool": operation_id}
    tool = _tool_for(op, annotations.get(op.operation_id))
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.inputSchema,
        "outputSchema": tool.outputSchema,
        "annotations": tool.annotations.model_dump(exclude_none=True) if tool.annotations else None,
    }


def _register_discovery_tool_handlers(
    server: Server,
    operations: dict[str, OperationSpec],
    annotations: dict[str, ToolAnnotation],
    client: CmpApiClient,
    extra_tools: Sequence[ExtraTool] = (),
) -> None:
    """Like `_register_tool_handlers`, but adds a discovery layer
    (`search_tools`, `get_tool_schema`) alongside every real operation and
    `extra_tools` -- built for servers large enough that a handful of
    rarely-needed operations are worth being findable by search rather than
    only by exact name.

    2026-09-26: this server is now piped through `guardian-admin` as a
    proxied external connection (see the top-level workspace CLAUDE.md's
    "proxy-gateway" section) rather than reachable directly -- dispatch
    logging/approval-gating and per-tool enable/disable both moved to that
    single proxy layer (`guardian_platform.admin_mcp.proxy`, `config_store`'s
    unified tool registry), so this handler is now a raw, ungated dispatch,
    same shape `openstack-ops`/`openstack-logs` (servers this project never
    owned the code of) always had to be. `_list_tools` no longer filters by
    the old curated `hidden`/`resource_spec` fields either -- it returns
    every real operation, unfiltered; enabled/disabled is the proxy's
    registry's job now, and MCP resources were dropped project-wide the
    same day (see `resources.py`'s own removal), so `resource_spec` no
    longer means anything. `extra_tools`' own `requires_approval`/
    `async_approval` flags (this server's few hand-built tools, e.g. legacy
    operations) are also no longer honored here for the same reason -- an
    `ExtraTool` that still needs gating should get a real `risk_level`
    through the registry like any other tool."""
    extra_by_name = {extra.tool.name: extra for extra in extra_tools}

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            *(_tool_for(op, annotations.get(op.operation_id)) for op in operations.values()),
            *(extra.tool for extra in extra_tools),
            _search_tools_meta_tool(),
            _get_tool_schema_meta_tool(),
        ]

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        extra = extra_by_name.get(tool_name)
        if extra is not None:
            return extra.handler(arguments)
        if tool_name == "search_tools":
            return _run_search_tools(operations, annotations, arguments)
        if tool_name == "get_tool_schema":
            return _run_get_tool_schema(operations, annotations, arguments)
        op = operations.get(tool_name)
        if op is None:
            return {"error": "unknown_tool", "tool": tool_name}
        return client.call(op, arguments)


def build_server(
    name: str,
    spec_path: Path,
    env_prefix: str,
    annotations_path: Path | None = None,
    extra_operations: dict[str, OperationSpec] | None = None,
) -> Server:
    """Build an MCP server exposing every operation in `spec_path` as one tool.

    `annotations_path` points at a curated operationId -> guidance overlay
    (see `ToolAnnotation`); omitted or missing means every tool falls back to
    a method-derived hint and the raw spec text -- still functional, just
    thinner guidance for an agent deciding whether to call it.

    `extra_operations` merges in hand-defined operations that aren't in the
    spec at all -- e.g. a legacy endpoint the current API no longer
    documents. Kept generic here (the bridge doesn't know what "legacy"
    means for any particular API); a caller decides which extras to pass,
    typically gated behind its own opt-in flag.
    """
    operations = {**load_operations(spec_path), **(extra_operations or {})}
    annotations = load_annotations(annotations_path) if annotations_path else {}
    client = CmpApiClient.from_env(env_prefix)
    server: Server = Server(name)
    _register_tool_handlers(server, operations, annotations, client)
    return server


@dataclass(frozen=True)
class SpecSource:
    """One OpenAPI document to merge into a multi-spec server (see `build_multi_spec_server`)."""

    spec_path: Path
    annotations_path: Path | None = None
    extra_operations: dict[str, OperationSpec] | None = None


@dataclass(frozen=True)
class ExtraTool:
    """A hand-built tool with no backing OpenAPI operation at all -- e.g. one that
    searches a local knowledge base instead of calling the CMP API. `handler`
    receives the raw call_tool arguments and returns the result dict directly,
    the same convention every operation-backed tool follows. Always listed in
    a discovery-enabled server's `list_tools()`, same as the built-in
    `search_tools`/`get_tool_schema` meta-tools -- there's no `hidden` concept
    for one of these, since there's no annotation overlay entry for something
    that isn't an operationId; `list_tools()` visibility isn't optional here.

    Kept generic here on purpose: the bridge has no idea what "failure
    patterns" or any other domain concept means, only that some server wants
    to bolt on a tool shaped like this -- the same reasoning as
    `SpecSource.extra_operations` for hand-defined operations.

    `requires_approval` defaults to `False` (a local lookup like
    search_failure_patterns needs no gate) but a hand-built tool can opt in
    -- e.g. one that, unlike every operation-backed tool, has no OpenAPI
    spec/curated annotation to derive a `tool_category` from at all, but
    still represents a real decision point an operator should see and
    approve before it resolves.

    `async_approval` (only meaningful alongside `requires_approval=True`)
    passes through to `telemetry.instrument_dispatch`'s same-named
    parameter -- see its docstring. `submit_investigation_plan`/
    `..._report` are the current users: their handlers have no real
    external effect, so it's safe for `dispatch()` to run immediately while
    the pending row itself waits for an operator's decision.

    `annotation`, if set, is purely for the admin GUI's own catalog/section
    display (e.g. `ToolAnnotation(section="system")`) -- unlike an
    operation's own curated annotation, it plays no role in gating or
    `list_tools()` visibility here (both already handled by the two fields
    above and the "always listed" rule respectively).
    """

    tool: types.Tool
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    requires_approval: bool = False
    async_approval: bool = False
    annotation: ToolAnnotation | None = None


def merge_operations(sources: Sequence[SpecSource]) -> dict[str, OperationSpec]:
    """The operation-merging half of `build_multi_spec_server`, exposed on its
    own for a caller that also needs the merged operation set directly (e.g.
    to address some of those same operations from resource templates) without
    re-implementing collision detection by hand.

    Raises `ValueError` naming both source spec paths on any operation_id
    collision, rather than letting a later source silently shadow an earlier
    one -- a guard against a future spec change, not a known problem with the
    specs this is built against today.
    """
    operations: dict[str, OperationSpec] = {}
    origin: dict[str, Path] = {}
    for source in sources:
        source_operations = {
            **load_operations(source.spec_path),
            **(source.extra_operations or {}),
        }
        for operation_id, op in source_operations.items():
            if operation_id in operations:
                raise ValueError(
                    f"operation_id collision: {operation_id!r} appears in both "
                    f"{origin[operation_id]} and {source.spec_path}"
                )
            operations[operation_id] = op
            origin[operation_id] = source.spec_path
    return operations


TOOL_ANNOTATION_FIELD_NAMES = frozenset(f.name for f in fields(ToolAnnotation))


def apply_annotation_override(
    curated: ToolAnnotation | None, override: Mapping[str, Any] | None
) -> ToolAnnotation | None:
    """Merge a sparse per-tool override dict onto a curated `ToolAnnotation`
    (or a fresh default one if there was no curated entry at all yet).
    `preconditions`/`related_tools` get coerced from list to tuple (JSON has
    no tuple type of its own); unknown keys are silently dropped rather than
    raising, so a stale or hand-edited config-store row can never crash a
    caller. Shared by `build_multi_spec_server` (applies overrides to the
    actual live tool set) and any caller that needs the identical merge
    purely for display (e.g. `cmp_admin_mcp.main`'s catalog helpers) -- one
    merge implementation, not two that could quietly drift apart.
    """
    if not override:
        return curated
    base = curated.__dict__ if curated is not None else {}
    safe_override = {k: v for k, v in override.items() if k in TOOL_ANNOTATION_FIELD_NAMES}
    merged = {**base, **safe_override}
    if isinstance(merged.get("preconditions"), list):
        merged["preconditions"] = tuple(merged["preconditions"])
    if isinstance(merged.get("related_tools"), list):
        merged["related_tools"] = tuple(merged["related_tools"])
    return ToolAnnotation(**merged)


def build_multi_spec_server(
    name: str,
    sources: Sequence[SpecSource],
    env_prefix: str,
    *,
    instructions: str | None = None,
    extra_tools: Sequence[ExtraTool] = (),
    annotation_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> Server:
    """Build one MCP server merging every operation across multiple specs under
    one shared client/credential set.

    For API domains that are really one coherent backend split across
    multiple OpenAPI documents only because that's how the upstream API
    happens to be documented (e.g. CMP admin-v2's server/network/block-storage
    specs, which share one base URL and one set of credentials) -- `
    build_server` stays the right choice for a genuinely separate API domain.

    Uses progressive discovery (see `_register_discovery_tool_handlers`):
    `list_tools` returns every operation except the ones curated as
    `hidden=True`, plus any `extra_tools` (hand-built tools with no backing
    operation, e.g. a knowledge-base search), plus the `search_tools`/
    `get_tool_schema` meta-tools -- not necessarily the full merged set,
    which is appropriate once that set is large enough (100+ operations
    here) that listing every single one by default would cost more context
    than it saves for the rarely-needed tail. Every operation and extra tool
    stays callable by exact name regardless of visibility.

    `annotation_overrides` lets a caller apply a sparse per-operation
    `ToolAnnotation` field overlay (any of `hidden`/`section`/`risk_level`/
    `tool_category`/`usage_note`/`preconditions`/`related_tools`/
    `read_only`/`destructive`/`idempotent`) without editing the curated
    annotations file -- a plain `{operation_id: {field: value, ...}}`
    mapping, applied after the curated annotations are merged. Unknown keys
    are silently dropped rather than raising, so a stale or hand-edited
    config-store row can never crash server startup. This module has no
    idea where such a mapping might come from (a config store, a GUI, a
    test); it only knows how to apply one -- and because it's applied here,
    to the actual live annotations dict `_register_discovery_tool_handlers`
    uses, an override now affects what an agent really sees via
    `list_tools()`/`get_tool_schema`, not just what a catalog UI displays.
    """
    operations = merge_operations(sources)
    annotations: dict[str, ToolAnnotation] = {}
    for source in sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    for operation_id, override_fields in (annotation_overrides or {}).items():
        if not override_fields:
            continue
        merged = apply_annotation_override(annotations.get(operation_id), override_fields)
        if merged is not None:
            annotations[operation_id] = merged

    client = CmpApiClient.from_env(env_prefix)
    server: Server = Server(name, instructions=instructions)
    _register_discovery_tool_handlers(server, operations, annotations, client, extra_tools)
    return server


async def _serve_stdio(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(server: Server) -> None:
    """Run `server` over stdio until the client disconnects. Blocks."""
    anyio.run(_serve_stdio, server)


__all__ = [
    "TOOL_ANNOTATION_FIELD_NAMES",
    "CmpApiClient",
    "ExtraTool",
    "OperationSpec",
    "ParamSpec",
    "SpecSource",
    "ToolAnnotation",
    "action_for_operation",
    "annotations_for",
    "apply_annotation_override",
    "build_input_schema",
    "build_multi_spec_server",
    "build_server",
    "description_for",
    "first_sentence",
    "load_annotations",
    "load_operations",
    "merge_operations",
    "run_stdio",
]
