# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`cmp-mcp`: the CMP-level MCP in Guardian's multi-MCP controller (see the
top-level [CLAUDE.md](../CLAUDE.md)) — two MCP servers that expose a CMP
(cloud management platform) admin API and its Elasticsearch logs as tools
for Claude Code, paired with an `investigate-incident` skill that drives
incident response over these tools. The shared config/ticketing/telemetry
store, the admin GUI, and the ticketing/interaction tools all live one
level up in `guardian-platform/` (see its own
[CLAUDE.md](../guardian-platform/CLAUDE.md)) — this project consumes that
as an editable path dependency, but owns none of it.

## Commands

Install deps (uv-managed; `package = true` since `guardian-platform`'s admin
GUI depends back on this project for 3 narrow CMP-specific features — see
the top-level CLAUDE.md's `guardian-platform/` bullet):

```bash
uv sync --extra dev
```

Run tests:

```bash
uv run pytest
```

Run a single test file / test:

```bash
uv run pytest tests/test_openapi_bridge.py
uv run pytest tests/test_openapi_bridge.py::test_name -v
```

Lint / type-check (Python):

```bash
uv run ruff check .
uv run mypy .
```

Run an MCP server directly (normally spawned by Claude Code via `.mcp.json`):

```bash
uv run python -m mcp_servers.cmp_admin_mcp.main
uv run python -m mcp_servers.cmp_logs_mcp.main
```

The admin GUI (build/serve/dev-loop commands) and the `guardian-admin` MCP
server now live in `guardian-platform/` — see its own
[CLAUDE.md](../guardian-platform/CLAUDE.md) for those commands.

## Architecture

### MCP servers (`mcp_servers/`)

Both servers are thin `main.py` entry points built on a shared
OpenAPI-to-MCP-tool bridge in `mcp_servers/openapi_bridge.py`. That bridge
reads an OpenAPI spec JSON (`mcp_servers/specs/*.json`) plus a curated
"annotations" JSON (`mcp_servers/annotations/*.json` — usage notes, risk
level, tool category, pinned state, preconditions, related tools) and turns
each operation into an MCP `Tool`, with a discovery layer (`search_tools`,
`get_tool_schema`) rather than exposing hundreds of tools flat.

- **`cmp_admin_mcp`** — the main server. Merges multiple OpenAPI spec
  "domains" (`server`, `block_storage`, `network`, `server_v1`) onto one
  `Server` instance via `SpecSource`/`build_multi_spec_server`, plus:
  - `legacy_operations.py` — opt-in legacy operations (e.g.
    `get_volume_legacy`), gated behind
    `CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET=1`; off by default.
  - Spec "domains" are not mutually exclusive: `server` and `server_v1`
    cover the same resource under different API versions and can both be
    enabled since their `operationId`s live in different namespaces.
  - Runtime-configurable via the admin GUI (`guardian-platform/admin_gui/`):
    spec sources, annotation overrides, and pinned-tool overrides are all
    layered over the built-in JSON defaults through
    `guardian_platform.config_store`, which reads from the shared SQLite
    store (`guardian_platform.db`, `guardian-platform/data/guardian.db`). A
    server that never touches the GUI is unaffected — every config-store
    getter degrades to "nothing configured".
  - The ticket/plan/report tools (`start_investigate`,
    `submit_investigation_plan`, `submit_investigation_report`,
    `notify_admin`) that the `investigate-incident`
    skill drives used to live here (`investigation_reporting.py`) but moved
    to the `guardian-admin` MCP server (`guardian-platform/src/
    guardian_platform/admin_mcp/`) on 2026-09-14 — they were never
    CMP-specific, just historically bolted on here since this was the only
    MCP with an approval-gating dispatch layer at the time. The curated
    failure-pattern KB (`search_failure_patterns`, `failure_pattern_matcher.py`)
    moved the same day, for a related but different reason: the KB itself
    already spans both CMP-level and core-level failure patterns (see the
    skill's "CMP level vs core level" section), so it was never CMP-specific
    either — now `guardian_platform.failure_patterns`, exposed as a
    `guardian-admin` tool. Its `cmp://runbook/{pattern_id}` MCP resource
    (`resources.py`) used to stay here reading from that same relocated
    module, until MCP resources were dropped project-wide 2026-09-26 (see
    "Piped through guardian-admin" below) — there is no by-id lookup
    anymore, only `search_failure_patterns`' free-text match.
- **`cmp_logs_mcp`** — wraps Elasticsearch log search (`client.py`) with
  masking of sensitive fields (`mask.py`, backed by
  `guardian_platform.masking`).

Configuration for both servers is env-var based per `_ENV_PREFIX` (e.g.
`CMP_ADMIN_V2_BASE_URL`, `CMP_ADMIN_V2_PAT` or
`CMP_ADMIN_V2_USERNAME`/`CMP_ADMIN_V2_PASSWORD`), read once at server-build
time — these are ephemeral stdio subprocesses re-spawned per proxy-worker
connect (see "Piped through guardian-admin" below), so there is
deliberately no live-reload path.

**Piped through `guardian-admin` as proxied external connections
(2026-09-26)**, exactly like `openstack-ops`/`openstack-logs` (see the
top-level workspace CLAUDE.md's "proxy-gateway" notes) — no longer spawned
directly by Claude Code via `.mcp.json`. Both servers' own `_call_tool`
handlers are now raw, ungated dispatch: the `instrument_dispatch` calls that
used to live inside `openapi_bridge.py`/`cmp_logs_mcp/server.py` are gone,
since logging/approval-gating moved entirely to
`guardian_platform.admin_mcp.proxy` and `config_store`'s unified tool
registry — calling either server directly (as this project's own unit
tests do) produces zero `events` rows, by design (see
`tests/test_event_logging.py`). `list_tools()` for both no longer filters
by the old curated `hidden` field either — every real operation is
returned, unfiltered; enabled/disabled is the registry's job now, applied
one layer up. The admin GUI's bespoke cmp-admin/cmp-logs credential-test UI
(`/test-credentials`) was retired the same day — as ordinary external
connections, their `CMP_ADMIN_V2_*`/`CMP_LOGS_ES_*` credentials are edited
via the generic `env` dict any external connection uses, with no dedicated
validation button (same as `openstack-ops` already had). MCP *resources*
(`resources.py`, e.g. `cmp://elastic-ip/{id}`) and the `assemble_log` MCP
*prompt* were dropped the same day too — every resource was already backed
by a plain callable tool (just excluded from default `list_tools()` output,
which no longer filters that way either), except `cmp://runbook/
{pattern_id}` (curated failure-pattern lookup by id), which has no tool
replacement — use `search_failure_patterns`' free-text signature match
instead. See `guardian_platform.migrations.pipe_cmp_through_guardian_admin`
for the one-time connection-conversion migration.

`mcp_servers/cmp_admin_mcp/migrations/backfill_tool_visibility.py` is a
one-time, hand-run migration that stayed here (rather than moving to
`guardian-platform` with the rest of the shared layer, see below) since
it's genuinely CMP-admin-specific: hardcodes `_SERVER_ID = "cmp-admin"` and
imports `mcp_servers.cmp_admin_mcp.main` directly. It's the one every
existing `data/guardian.db` needed exactly once, when `ToolAnnotation.hidden`
(opt-out — visible unless explicitly hidden) replaced the old opt-in
`pinned` field: it backfills a `tool_annotation_overrides` row for every
cmp-admin operation that was never explicitly pinned, so the polarity flip
doesn't silently make them all visible. Already run against this repo's own
`guardian-platform/data/guardian.db`, and the 4 curated JSON files under
`mcp_servers/annotations/` already carry `hidden` instead of `pinned` — both
needed together, since the JSON rewrite alone doesn't fix a database with
its own GUI-set overrides, and the DB backfill alone doesn't fix a fresh
install with no DB overrides at all. Only relevant again if some other,
older `guardian.db` predating this migration is ever pointed at this
codebase: `uv run python -m
mcp_servers.cmp_admin_mcp.migrations.backfill_tool_visibility`.

### Admin GUI, ticketing, and the shared platform layer

All moved to `guardian-platform/` on 2026-09-14 — the admin GUI
(`admin_gui/`, FastAPI + React), the ticket/comment-reopen/auto-investigate
machinery (`agent_resume.py`, `auto_investigate.py`), the `guardian-admin`
MCP server (ticket/plan/report/notify tools), and the shared
config/ticketing/telemetry/masking library
(`guardian_platform.{db,config_store,masking,tickets,telemetry}`, backed by
`guardian-platform/data/guardian.db`) that both this project and the admin
GUI read/write. None of it was ever CMP-specific — `cmp-mcp` consumes it as
an editable path dependency, same as `guardian-platform`'s admin GUI
reaches back into 2 narrow CMP-specific features here (native tool-probing,
spec-source CRUD — failure-pattern KB CRUD used to be a 3rd, but that KB
moved into guardian-platform on 2026-09-14 too). See
[`guardian-platform/CLAUDE.md`](../guardian-platform/CLAUDE.md) for the
full architecture of all of this, including the "Reopening via comments"
and "Auto-investigating error logs" behavior.

### Mock stack (`mock/`)

Local stand-ins for the two external systems `cmp_admin_mcp`/`cmp_logs_mcp`
talk to, for exercising real HTTP round trips without a real CMP/Elasticsearch
deployment. Neither is used by the automated test suite (`tests/` uses
`httpx.MockTransport` instead) — these are for manual/interactive runs only.

**2026-09-27: `cmp-admin`'s real tool catalog is deliberately trimmed to exactly the 7 operations
the failure-pattern KB actually names or implies** — CMP's own admin API is mid-migration from v1
to v2 and not yet well-defined on the v2 side, so rather than exposing the full ~85-operation
surface (`server_v1`/`block_storage`/`network` domains merged), the `server` domain is temporarily
pinned to `server_v1` only (`guardian_platform.config_store.upsert_domain_spec_source('cmp-admin',
'server', enabled=False)` + `('cmp-admin', 'server_v1', enabled=True)`) and every operation outside
the 7 below is `enabled=False` in the unified tool registry (real access control via
`guardian_platform.admin_mcp.proxy`, not just display filtering): `admin_api_servers_retrieve`/
`list`/`action_log`/`recreate` (v1 — including the fix for the 3 KB entries that literally say
"rebuild_server", v2's name for the same idea, since the server domain is v1-only for now),
`get_elastic_ip`/`get_private_ip` (v2 network domain, untouched by the server v1/v2 decision), and
`get_volume_legacy` (opt-in, `CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET=1` — the only way to satisfy
`volume_status_drift`'s volume-status check; still an unverified stopgap per its own module
docstring). Revisit both the domain pin and the trimmed set once CMP's v2 surface is more settled.

- **`mock/cmp_server/`** — a dependency-free `http.server` stand-in for the
  CMP admin API (`python mock/cmp_server/server.py`, `:8081` by default). One
  seeded incident (server `4a76a7df-f2dd-479f-bcf7-118a19f71c40`, *found but
  suspended*) is genuinely stateful — calling `admin_api_servers_recreate` on
  it flips it to `active`/`running` in memory, so it can be driven through a
  real find-diagnose-fix-verify cycle. Every other id/path/method gets a
  generic `200 {METHOD} {PATH}` echo, enough to confirm request shape without
  faking ~100 other response bodies. See `mock/cmp_server/README.md`.
- **`mock/elasticsearch/`** — a real single-node Elasticsearch + Kibana via
  `docker compose up -d`, seeded by `python seed.py` from `mock-logs-23.csv`
  (a real Kibana Discover export, 14,856 rows, exactly one seeded ERROR — a
  `cinderclient.exceptions.NotFound` matching the `volume_not_found` KB
  entry). See `mock/elasticsearch/README.md`.

Point the real servers at either with the same env vars used against a real
deployment (`CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081`,
`CMP_LOGS_ES_URL=http://localhost:9200` /
`CMP_LOGS_ES_INDEX=iaas-api-2026.08.23`) — both clients are mock-agnostic, so
nothing else changes.

### The `investigate-incident` skill (`.claude/skills/investigate-incident/`)

Drives incident response across both levels of Guardian's multi-MCP controller: **CMP level**
(`cmp-admin`/`cmp-logs`) and **core level** (`openstack-ops`/`openstack-logs`, the real
OpenStack cluster). Ticketing is always `guardian-admin` (`start_investigate` first, auto-attributes
subsequent tool calls; reports back via `submit_investigation_plan`/`submit_investigation_report`),
but evidence-gathering and remediation deliberately switch to core level for ground-truth
status cross-checks against CMP's own cached state, live migration/scheduler actions, compute
capacity/service health, and raw OpenStack-service-level log diagnosis (`openstack-logs`) --
see the skill's own "CMP level vs core level" section for the concrete, growing responsibility
split. MCP prompts/resources were dropped project-wide 2026-09-26 (see "Piped through
guardian-admin" above) -- there was never an `assemble_log` tool to call, and CMP-side ground
truth now reads via a plain tool call (`admin_api_servers_retrieve`, `get_elastic_ip`, etc.)
wherever a resource used to cover it.
