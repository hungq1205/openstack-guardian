"""Entry point for cmp-admin: every CMP admin-v2 server/network/block-storage
operation, merged onto one Server instance under one shared credential set.

Piped through `guardian-admin` as a proxied external connection now
(2026-09-26, see the top-level workspace CLAUDE.md's "proxy-gateway"
notes) -- dispatch logging/approval-gating and enable/disable both moved
to that single layer (`guardian_platform.admin_mcp.proxy`, `config_store`'s
unified tool registry), so this server's own dispatch (in
`openapi_bridge._register_discovery_tool_handlers`) is a raw, ungated pass-
through. MCP resources and the `assemble_log` prompt were dropped the same
day -- every resource this server used to serve was already backed by a
plain callable tool (just excluded from default `list_tools()` output,
which no longer filters that way either), except `cmp://runbook/
{pattern_id}`, which has no tool replacement (use `search_failure_patterns`
instead -- a free-text signature match, not a by-id lookup, but sufficient
for this workspace's needs).

The ticket/plan/report/notify tools that used to live here
(investigation_reporting.py) and the curated failure-pattern knowledge base
(failure_pattern_matcher.py) both moved to the `guardian-admin` MCP server
(2026-09-14) -- neither was actually CMP-specific, just historically bolted
on here since cmp-admin was the only MCP with an approval-gating dispatch
layer at the time (ticketing), or just physically nested in the one MCP
project that happened to build it first (the KB, which already spans both
CMP-level and core-level failure patterns). See
`guardian-platform/src/guardian_platform/admin_mcp/` and
`guardian_platform/failure_patterns.py`.

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

from guardian_platform.config_store import get_spec_sources, get_tool_annotation_overrides
from mcp.server.lowlevel import Server

from mcp_servers.cmp_admin_mcp.legacy_operations import LEGACY_OPERATIONS
from mcp_servers.openapi_bridge import (
    ExtraTool,
    OperationSpec,
    SpecSource,
    build_multi_spec_server,
    run_stdio,
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


def _extra_tools() -> list[ExtraTool]:
    """Hand-built (non-OpenAPI) tools this server bolts on -- currently none.
    `search_failure_patterns`, the last one, moved to `guardian-admin`
    (2026-09-14, see `guardian_platform.failure_patterns`) since it was never
    actually CMP-specific. Kept as its own function (rather than removed
    outright) so the catalog functions below can list whatever's here --
    with its own curated `annotation` -- alongside the OpenAPI-backed
    operations, rather than `all_operations_with_visibility()`'s "full
    catalog" silently meaning "full *OpenAPI* catalog only", if a genuinely
    CMP-specific extra tool ever gets added here again."""
    return []


def build_admin_server() -> Server:
    the_sources = sources()
    return build_multi_spec_server(
        "cmp-admin",
        the_sources,
        env_prefix=_ENV_PREFIX,
        extra_tools=_extra_tools(),
        annotation_overrides=get_tool_annotation_overrides("cmp-admin"),
    )


def main() -> None:
    run_stdio(build_admin_server())


if __name__ == "__main__":
    main()
