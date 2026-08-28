"""Entry point for cmp-admin: every CMP admin-v2 server/network/block-storage
operation, the curated failure-pattern knowledge base, resource templates,
the assemble_log prompt, and the ticket/plan/report tools
(investigation_reporting.py), merged onto one Server instance under one
shared credential set.

Configure via env vars: CMP_ADMIN_V2_BASE_URL, plus either CMP_ADMIN_V2_PAT
or CMP_ADMIN_V2_USERNAME/CMP_ADMIN_V2_PASSWORD. Run with `uv run python -m
mcp_servers.cmp_admin_mcp.main`.

Set CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET=1 to also register the legacy
get_volume_legacy operation (see legacy_operations.py) -- off by default,
since unlike rebuild_server this isn't a confirmed-working replacement for
anything, just an unverified stopgap for a gap in the current spec.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.lowlevel import Server

from mcp_servers.cmp_admin_mcp.failure_pattern_matcher import build_search_failure_patterns_tool
from mcp_servers.cmp_admin_mcp.investigation_reporting import (
    build_start_investigate_tool,
    build_submit_investigation_plan_tool,
    build_submit_investigation_report_tool,
)
from mcp_servers.cmp_admin_mcp.legacy_operations import LEGACY_OPERATIONS
from mcp_servers.cmp_admin_mcp.resources import build_resource_templates
from mcp_servers.openapi_bridge import (
    CmpApiClient,
    OperationSpec,
    SpecSource,
    ToolAnnotation,
    annotations_for,
    build_multi_spec_server,
    load_annotations,
    merge_operations,
    register_resource_templates,
    run_stdio,
)
from mcp_servers.prompts import register_prompts
from mcp_servers.shared.config_store import (
    get_annotation_overrides,
    get_pinned_overrides,
    get_spec_sources,
)

_SPECS_DIR = Path(__file__).resolve().parent.parent / "specs"
_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "annotations"
_ENV_PREFIX = "CMP_ADMIN_V2"
_LEGACY_VOLUME_GET_ENV_VAR = "CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET"

BUILT_IN_SPEC_DOMAINS: tuple[str, ...] = ("server", "block_storage", "network", "server_v1")
"""Every domain the admin GUI's Spec Sources page always shows one row for
-- editable, and toggleable there (see `SpecSourceRow.domain`), unlike a
purely additive extra pair. Not mutually exclusive with each other: two
domains covering the same real resource under different API versions
(`server` vs. `server_v1`) can both be enabled at once without conflict,
since their operations use different operationId namespaces -- toggling
one off and the other on is just how you "switch" between them."""

_DEFAULT_ENABLED_DOMAINS: frozenset[str] = frozenset({"server", "block_storage", "network"})
"""Which `BUILT_IN_SPEC_DOMAINS` ship active out of the box. `server_v1` --
the legacy /admin-api server surface, kept for environments still partly on
it -- ships alongside `server` but starts disabled, so a fresh install's
tool catalog is unchanged until an administrator opts into it."""


def legacy_operations_if_enabled() -> dict[str, OperationSpec] | None:
    """Return the legacy operation set only when explicitly opted into."""
    if os.environ.get(_LEGACY_VOLUME_GET_ENV_VAR, "").lower() in {"1", "true", "yes"}:
        return LEGACY_OPERATIONS
    return None


def domain_enabled_by_default(domain: str) -> bool:
    """Whether an untouched (never edited/toggled) `domain` row starts
    active -- `True` for the 3 original built-ins, `False` for anything
    shipped alongside them but opt-in (`server_v1`)."""
    return domain in _DEFAULT_ENABLED_DOMAINS


def default_spec_source(domain: str) -> SpecSource:
    """The hardcoded pair for one built-in domain, before any GUI edit."""
    extra_operations = legacy_operations_if_enabled() if domain == "block_storage" else None
    return SpecSource(
        _SPECS_DIR / f"{domain}.json", _ANNOTATIONS_DIR / f"{domain}.json", extra_operations=extra_operations
    )


def sources() -> list[SpecSource]:
    """The 3 built-in spec/annotation pairs, plus whatever an administrator
    has configured through the admin GUI's Spec Sources page.

    Read-only, same as every other config-store lookup here: a server that
    never touches the GUI never writes to `data/guardian.db` and gets
    exactly the 3 hardcoded pairs, untouched. The GUI's Spec Sources page
    presents all 3 built-in domains as first-class, always-visible rows
    (editable and removable there) by *synthesizing* the untouched ones from
    `default_spec_source` -- see `admin_gui/backend/routers/spec_sources.py`
    -- rather than this function ever needing to write a seed row itself.

    A configured row with no `domain` is additive, sitting alongside all
    built-ins. A configured row *with* a `domain` is that domain's edited
    version, if enabled, or an explicit disable/removal of it, if not --
    dropped entirely rather than falling back to the default, which is the
    whole point of being able to turn one off. `build_resource_templates`
    degrades gracefully (skips, doesn't crash) if that removes an operation
    a resource template needed -- see resources.py.
    """
    rows = get_spec_sources("cmp-admin", enabled_only=False)
    by_domain = {row.domain: row for row in rows if row.domain is not None}
    extras = [
        SpecSource(Path(row.spec_path), Path(row.annotations_path) if row.annotations_path else None)
        for row in rows
        if row.domain is None and row.enabled
    ]

    result = []
    for domain in BUILT_IN_SPEC_DOMAINS:
        row = by_domain.get(domain)
        if row is None:
            if domain not in _DEFAULT_ENABLED_DOMAINS:
                continue
            result.append(default_spec_source(domain))
        elif row.enabled:
            result.append(
                SpecSource(
                    Path(row.spec_path),
                    Path(row.annotations_path) if row.annotations_path else None,
                    extra_operations=legacy_operations_if_enabled() if domain == "block_storage" else None,
                )
            )
        # else: explicitly disabled/removed -- omit this domain entirely.
    return result + extras


def build_admin_server() -> Server:
    the_sources = sources()
    server = build_multi_spec_server(
        "cmp-admin",
        the_sources,
        env_prefix=_ENV_PREFIX,
        extra_tools=[
            build_search_failure_patterns_tool(),
            build_start_investigate_tool(),
            build_submit_investigation_plan_tool(),
            build_submit_investigation_report_tool(),
        ],
        pinned_overrides=get_pinned_overrides(),
    )
    operations = merge_operations(the_sources)
    annotations: dict[str, ToolAnnotation] = {}
    for source in the_sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    client = CmpApiClient.from_env(_ENV_PREFIX)
    register_resource_templates(server, build_resource_templates(operations, annotations, client))
    register_prompts(server)
    return server


def all_operations_with_pinned_state() -> list[dict[str, Any]]:
    """The full merged operation catalog (not the discovery-filtered subset
    `list_tools()` returns), each with its current effective `pinned` state --
    the data the admin GUI's pinned-tools toggle page needs. An override in
    the config store wins; otherwise the curated annotation's own `pinned`
    field; otherwise `False`, same precedence `build_admin_server` itself
    applies via `pinned_overrides`.
    """
    the_sources = sources()
    operations = merge_operations(the_sources)
    annotations: dict[str, ToolAnnotation] = {}
    for source in the_sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    pinned_overrides = get_pinned_overrides()
    annotation_overrides = get_annotation_overrides()

    catalog = []
    for operation_id, op in sorted(operations.items()):
        curated = _effective_annotation(annotations.get(operation_id), annotation_overrides.get(operation_id))
        hints = annotations_for(op, curated)
        pinned = pinned_overrides.get(operation_id, curated.pinned if curated is not None else False)
        catalog.append(
            {
                "operation_id": operation_id,
                "summary": op.summary,
                "category": curated.tool_category if curated else None,
                "risk_level": curated.risk_level if curated else None,
                "read_only": bool(hints.readOnlyHint),
                "destructive": bool(hints.destructiveHint),
                "pinned": pinned,
            }
        )
    return catalog


def _effective_annotation(
    curated: ToolAnnotation | None, override: dict[str, Any] | None
) -> ToolAnnotation | None:
    """A GUI edit (`annotation_overrides`) layered field-by-field over the
    curated JSON annotation -- an edit to one field never wipes the others,
    whether they came from the JSON file or an earlier edit."""
    if not override:
        return curated
    base = curated.__dict__ if curated is not None else {}
    merged = {**base, **override}
    if "preconditions" in merged and isinstance(merged["preconditions"], list):
        merged["preconditions"] = tuple(merged["preconditions"])
    if "related_tools" in merged and isinstance(merged["related_tools"], list):
        merged["related_tools"] = tuple(merged["related_tools"])
    return ToolAnnotation(**merged)


def operation_detail(operation_id: str) -> dict[str, Any] | None:
    """Everything known about one operation -- full spec (parameters, body/
    output schema) plus the curated annotation with any GUI edit applied --
    for the catalog page's click-through detail/edit view. `None` if no such
    operation exists in the current merged catalog."""
    the_sources = sources()
    operations = merge_operations(the_sources)
    op = operations.get(operation_id)
    if op is None:
        return None
    annotations: dict[str, ToolAnnotation] = {}
    for source in the_sources:
        if source.annotations_path is not None:
            annotations.update(load_annotations(source.annotations_path))
    curated = _effective_annotation(annotations.get(operation_id), get_annotation_overrides().get(operation_id))
    hints = annotations_for(op, curated)
    pinned = get_pinned_overrides().get(operation_id, curated.pinned if curated is not None else False)
    return {
        "operation_id": op.operation_id,
        "method": op.method,
        "path": op.path,
        "summary": op.summary,
        "description": op.description,
        "parameters": [
            {"name": p.name, "location": p.location, "required": p.required, "schema": p.schema}
            for p in op.parameters
        ],
        "body_schema": op.body_schema,
        "body_required": op.body_required,
        "output_schema": op.output_schema,
        "usage_note": curated.usage_note if curated else "",
        "category": curated.tool_category if curated else None,
        "risk_level": curated.risk_level if curated else None,
        "preconditions": list(curated.preconditions) if curated else [],
        "related_tools": list(curated.related_tools) if curated else [],
        "read_only": bool(hints.readOnlyHint),
        "destructive": bool(hints.destructiveHint),
        "idempotent": hints.idempotentHint,
        "pinned": pinned,
    }


def main() -> None:
    run_stdio(build_admin_server())


if __name__ == "__main__":
    main()
