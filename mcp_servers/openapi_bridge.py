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
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from mcp_servers.shared.config_store import get_config_value
from mcp_servers.shared.telemetry import instrument_dispatch

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
class ResourceSpec:
    """Configuration for exposing an operation as a resource template.

    A read-only operation can be exposed both ways or just as a resource,
    eliminating redundancy. `uri_template` defines the resource URI, `id_param`
    is the operation's parameter name to extract the id from the URI.
    Other fields allow operation-agnostic resource construction (e.g., for
    knowledge-base lookups, or specs with different parameter naming).
    """

    uri_template: str
    name: str
    description: str
    id_param: str | None = None
    """For operation-backed resources: the operation's id parameter name.
    Omit for knowledge-base/custom resolvers."""


@dataclass(frozen=True)
class ToolAnnotation:
    """Curated, agent-facing guidance for one operation that no OpenAPI field carries.

    Sparse by design -- only operations someone has actually looked at need
    an entry. Everything else falls back to the method-based hint and the
    raw spec text. `risk_level`/`tool_category` are this project's own
    vocabulary (see the design plan); `read_only`/`destructive`/`idempotent`
    override the mechanical HTTP-method guess when set. `resource_spec` exposes
    this read-only operation as a resource template instead of a tool,
    eliminating redundancy.
    """

    usage_note: str = ""
    risk_level: str | None = None
    tool_category: str | None = None
    preconditions: tuple[str, ...] = ()
    related_tools: tuple[str, ...] = ()
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    pinned: bool = False
    """True means always listed in a discovery-enabled server's `list_tools()`
    (see `build_multi_spec_server`) -- everything else is still callable via
    `call_tool`, just found through `search_tools`/`get_tool_schema` first."""
    resource_spec: ResourceSpec | None = None
    """If set, this read-only operation is exposed as a resource template
    (via cmp://{name}/{id}) instead of a tool. The operation must be a GET
    with a single required id-parameter (the resource id)."""


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
            pinned=bool(entry.get("pinned", False)),
            resource_spec=(
                ResourceSpec(
                    uri_template=rs["uri_template"],
                    name=rs["name"],
                    description=rs["description"],
                    id_param=rs.get("id_param"),
                )
                if (rs := entry.get("resource_spec")) is not None
                else None
            ),
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
    returns -- `{"status_code": ..., "data": <resolved>}` on success, or
    `{"error": ..., "message": ...}` on failure -- never the bare resolved
    schema at the top level. Declaring `outputSchema` as the bare schema
    would fail the SDK's own output validation on every real call, since the
    real `structuredContent` is always one of these two envelopes.
    """
    return {
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "properties": {"status_code": {"type": "integer"}, "data": resolved},
                "required": ["status_code", "data"],
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
        return [
            _tool_for(op, annotations.get(op.operation_id))
            for op in operations.values()
            if (anno := annotations.get(op.operation_id)) is None or anno.resource_spec is None
        ]

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
            "Search every operation available on this server, including ones not shown by "
            "default -- most tools here are reachable by exact name even though list_tools "
            "only surfaces a small pinned subset. Use this first to find the right "
            "operation_id, then get_tool_schema for its exact input shape before calling it."
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
    """Like `_register_tool_handlers`, but `list_tools` returns only the curated
    `pinned` subset plus any `extra_tools` plus two meta-tools (`search_tools`,
    `get_tool_schema`) instead of every operation -- built for servers large
    enough that dumping every tool into an agent's context by default would
    cost more than it helps. `call_tool` still dispatches to any real
    operation by exact name regardless of whether it was listed, so nothing
    becomes uncallable, only unlisted by default."""
    extra_by_name = {extra.tool.name: extra for extra in extra_tools}

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        pinned = [
            _tool_for(op, annotations[op.operation_id])
            for op in operations.values()
            if (
                annotations.get(op.operation_id) is not None
                and annotations[op.operation_id].pinned
                and annotations[op.operation_id].resource_spec is None
            )
        ]
        return [
            *pinned,
            *(extra.tool for extra in extra_tools),
            _search_tools_meta_tool(),
            _get_tool_schema_meta_tool(),
        ]

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def _dispatch() -> dict[str, Any]:
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

        op = operations.get(tool_name)
        curated = annotations.get(tool_name) if op is not None else None
        extra = extra_by_name.get(tool_name)
        requires_approval = (
            extra.requires_approval
            if extra is not None
            else curated is not None and curated.tool_category == "action"
        )
        return await instrument_dispatch(
            server=server.name,
            kind="tool",
            name=tool_name,
            arguments=arguments,
            source=f"{server.name}.tool.{tool_name}",
            dispatch=_dispatch,
            action=action_for_operation(op) if op is not None else None,
            requires_approval=requires_approval,
        )


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
    `search_tools`/`get_tool_schema` meta-tools -- there's no `pinned` flag to
    set since there's no annotation overlay entry for something that isn't an
    operationId.

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
    """

    tool: types.Tool
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    requires_approval: bool = False


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


def build_multi_spec_server(
    name: str,
    sources: Sequence[SpecSource],
    env_prefix: str,
    *,
    instructions: str | None = None,
    extra_tools: Sequence[ExtraTool] = (),
    pinned_overrides: Mapping[str, bool] | None = None,
) -> Server:
    """Build one MCP server merging every operation across multiple specs under
    one shared client/credential set.

    For API domains that are really one coherent backend split across
    multiple OpenAPI documents only because that's how the upstream API
    happens to be documented (e.g. CMP admin-v2's server/network/block-storage
    specs, which share one base URL and one set of credentials) -- `
    build_server` stays the right choice for a genuinely separate API domain.

    Uses progressive discovery (see `_register_discovery_tool_handlers`):
    `list_tools` returns only operations curated as `pinned=True`, plus any
    `extra_tools` (hand-built tools with no backing operation, e.g. a
    knowledge-base search), plus the `search_tools`/`get_tool_schema`
    meta-tools -- not the full merged set, which is appropriate once that set
    is large enough (100+ operations here) that listing everything by default
    would cost more context than it saves. Every operation and extra tool
    stays callable by exact name regardless of pinning.

    `pinned_overrides` lets a caller flip an operation's pinned state without
    editing its annotations file -- a plain `{operation_id: bool}` mapping,
    applied after the curated annotations are merged. This module has no idea
    where such a mapping might come from (a config store, a GUI, a test); it
    only knows how to apply one.
    """
    operations = merge_operations(sources)
    annotations: dict[str, ToolAnnotation] = {}
    for source in sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    for operation_id, pinned in (pinned_overrides or {}).items():
        existing = annotations.get(operation_id)
        annotations[operation_id] = (
            replace(existing, pinned=pinned)
            if existing is not None
            else ToolAnnotation(pinned=pinned)
        )

    client = CmpApiClient.from_env(env_prefix)
    server: Server = Server(name, instructions=instructions)
    _register_discovery_tool_handlers(server, operations, annotations, client, extra_tools)
    return server


_RESOURCE_URI_VARIABLE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class ResourceTemplateSpec:
    """One parameterized resource for `list_resource_templates`/`read_resource`.

    `uri_template` must have exactly one `{variable}` segment -- every
    resource this bridge exposes today resolves by a single id, so matching
    and extraction stays a simple regex swap rather than a full RFC 6570
    template engine as long as that holds; `register_resource_templates`
    raises at registration time if a template doesn't fit this shape.

    `resolver` receives the extracted id and returns the resolved record, or
    `None` if the id doesn't resolve to anything -- generic to any backing
    mechanism (a live API call via `operation_resolver`, an in-memory
    knowledge-base lookup, ...).
    """

    uri_template: str
    name: str
    description: str
    resolver: Callable[[str], dict[str, Any] | None]
    mime_type: str = "application/json"
    action: str | None = None
    """The human-readable "what actually runs" when this resource is read --
    an HTTP method + path for an operation-backed resolver (see
    `operation_resolver`), `None` for a purely local resolver (e.g. the
    runbook knowledge-base lookup). Surfaced in the events log's Action
    column."""


def operation_resolver(
    op: OperationSpec, param_name: str, client: CmpApiClient
) -> Callable[[str], dict[str, Any] | None]:
    """Build a `ResourceTemplateSpec.resolver` that reads one resource via a
    real, single-parameter GET operation -- the same `CmpApiClient` every
    tool call already goes through, just addressed as a resource instead of a
    tool call. Never actually returns `None` (a real 404 still comes back as
    a `{"status_code": 404, ...}` dict, per `CmpApiClient.call`) -- `None` is
    reserved for resolvers with a genuine "no such id" case, e.g. a
    knowledge-base lookup.
    """

    def _resolve(value: str) -> dict[str, Any] | None:
        return client.call(op, {param_name: value})

    return _resolve


def _compile_uri_template(uri_template: str) -> re.Pattern[str]:
    variables = _RESOURCE_URI_VARIABLE.findall(uri_template)
    if len(variables) != 1:
        raise ValueError(f"only single-variable URI templates are supported, got {uri_template!r}")
    placeholder_pattern = re.escape("{" + variables[0] + "}")
    body_pattern = re.escape(uri_template).replace(placeholder_pattern, "(?P<value>[^/]+)")
    return re.compile(f"^{body_pattern}$")


def register_resource_templates(server: Server, specs: Sequence[ResourceTemplateSpec]) -> None:
    """Wire `list_resources`/`list_resource_templates`/`read_resource` for a
    fixed set of parameterized, single-variable resource templates.

    `list_resources` always returns `[]` -- there are no enumerable concrete
    resources here, only templates -- but it must still be registered: a
    server only advertises the `resources` capability at all when
    `ListResourcesRequest` has a registered handler (see
    `Server.get_capabilities` in `mcp.server.lowlevel.server`), independent
    of whether `list_resource_templates`/`read_resource` are also registered.
    """
    compiled = [(spec, _compile_uri_template(spec.uri_template)) for spec in specs]

    @server.list_resources()
    async def _list_resources() -> list[types.Resource]:
        return []

    @server.list_resource_templates()
    async def _list_resource_templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                uriTemplate=spec.uri_template,
                name=spec.name,
                description=spec.description,
                mimeType=spec.mime_type,
            )
            for spec in specs
        ]

    @server.read_resource()
    async def _read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
        uri_text = str(uri)
        mime_type = "application/json"

        def _matching_spec() -> ResourceTemplateSpec | None:
            for spec, regex in compiled:
                if regex.match(uri_text) is not None:
                    return spec
            return None

        async def _dispatch() -> dict[str, Any]:
            nonlocal mime_type
            for spec, regex in compiled:
                match = regex.match(uri_text)
                if match is None:
                    continue
                mime_type = spec.mime_type
                record = spec.resolver(match.group("value"))
                if record is None:
                    record = {"error": "not_found", "uri": uri_text}
                return record
            return {"error": "unknown_resource_uri", "uri": uri_text}

        matched = _matching_spec()
        record = await instrument_dispatch(
            server=server.name,
            kind="resource",
            name=uri_text,
            arguments={},
            source=f"{server.name}.resource.{uri_text}",
            dispatch=_dispatch,
            action=matched.action if matched is not None else None,
        )
        return [ReadResourceContents(content=json.dumps(record), mime_type=mime_type)]


async def _serve_stdio(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(server: Server) -> None:
    """Run `server` over stdio until the client disconnects. Blocks."""
    anyio.run(_serve_stdio, server)


__all__ = [
    "CmpApiClient",
    "ExtraTool",
    "OperationSpec",
    "ParamSpec",
    "ResourceTemplateSpec",
    "SpecSource",
    "ToolAnnotation",
    "action_for_operation",
    "annotations_for",
    "build_input_schema",
    "build_multi_spec_server",
    "build_server",
    "description_for",
    "load_annotations",
    "load_operations",
    "merge_operations",
    "operation_resolver",
    "register_resource_templates",
    "run_stdio",
]
