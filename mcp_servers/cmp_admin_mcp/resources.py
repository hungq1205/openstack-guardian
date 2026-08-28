"""Curated resource templates for cmp-admin.

Resources are built dynamically from operation annotations (`resource_spec` field).
This keeps the resource catalog sync'd with the operation set without hardcoding
a separate list.

For extensibility: to add a new resource backed by an operation, add a
`resource_spec` field to that operation's annotation in the annotations JSON.
No code changes needed -- resources are purely data-driven.

Custom resolvers (e.g., knowledge-base lookups) can still be registered here
by calling `register_custom_resolver(...)` before `build_resource_templates()`.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp_servers.cmp_admin_mcp.failure_pattern_matcher import get_pattern_by_id
from mcp_servers.openapi_bridge import (
    CmpApiClient,
    OperationSpec,
    ResourceTemplateSpec,
    ToolAnnotation,
    action_for_operation,
    operation_resolver,
)

_CUSTOM_RESOLVERS: dict[str, Callable[[str], dict[str, Any] | None]] = {}


def register_custom_resolver(
    uri_template: str, resolver: Callable[[str], dict[str, Any] | None]
) -> None:
    """Register a custom resolver for a non-operation-backed resource.
    Call before `build_resource_templates()`.
    """
    _CUSTOM_RESOLVERS[uri_template] = resolver


def _runbook_resolver(pattern_id: str) -> dict[str, Any] | None:
    pattern = get_pattern_by_id(pattern_id)
    return pattern.raw if pattern is not None else None


def build_resource_templates(
    operations: dict[str, OperationSpec],
    annotations: dict[str, ToolAnnotation],
    client: CmpApiClient,
) -> list[ResourceTemplateSpec]:
    """Build resource templates from operation annotations.

    Scans all annotated operations for `resource_spec` fields. When found,
    creates a resource template (operation-backed via `operation_resolver`).
    Operations without a `resource_spec` remain tools only.

    Missing operations (disabled via Spec Sources) are silently skipped.

    `operations` must be the merged cmp-admin operation set (see
    `openapi_bridge.merge_operations`). `annotations` is the merged
    annotations dict (see `openapi_bridge.load_annotations`).
    """
    templates: list[ResourceTemplateSpec] = []

    for operation_id, annotation in annotations.items():
        if annotation.resource_spec is None:
            continue
        if operation_id not in operations:
            continue

        op = operations[operation_id]
        spec = annotation.resource_spec

        id_param = spec.id_param or operation_id.split("_")[-1]

        templates.append(
            ResourceTemplateSpec(
                uri_template=spec.uri_template,
                name=spec.name,
                description=spec.description,
                resolver=operation_resolver(op, id_param, client),
                action=action_for_operation(op),
            )
        )

    templates.extend(
        ResourceTemplateSpec(
            uri_template=uri_template,
            name=uri_template.split("/{")[0].replace("cmp://", ""),
            description="",
            resolver=resolver,
        )
        for uri_template, resolver in _CUSTOM_RESOLVERS.items()
    )

    templates.append(
        ResourceTemplateSpec(
            uri_template="cmp://runbook/{pattern_id}",
            name="runbook",
            description=(
                "One curated failure-pattern record by id (see search_failure_patterns) -- "
                "cause, instruction, and traceability. "
                "Reference knowledge to consult mid-investigation."
            ),
            resolver=_runbook_resolver,
        )
    )

    return templates


__all__ = ["build_resource_templates", "register_custom_resolver"]
