# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

"OpenStack Guardian" (`cmp-mcp`): a set of MCP servers that expose a CMP
(cloud management platform) admin API and its Elasticsearch logs as tools
for Claude Code, paired with a local admin GUI (FastAPI + React) for
configuring them, and an `investigate-incident` skill that drives incident
response over these tools.

## Commands

Install deps (uv-managed, `package = false` — this is not a package):

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
uv run python -m mcp_servers.cmp_notify_mcp.main
```

Admin GUI — build once, then serve API + built frontend together on `:8765`
(binds to `127.0.0.1` only, no auth — never expose beyond localhost):

```bash
cd admin_gui/frontend && npm install && npm run build
cd ../.. && uv run python -m admin_gui.backend.main
```

Frontend dev loop (Vite on `:5173`, proxies `/api` to the backend — see
`admin_gui/frontend/vite.config.ts`): run the backend as above, and
separately:

```bash
cd admin_gui/frontend && npm run dev
```

Frontend lint/build:

```bash
cd admin_gui/frontend && npm run lint
cd admin_gui/frontend && npm run build   # tsc -b && vite build
```

## Architecture

### MCP servers (`mcp_servers/`)

All three servers are thin `main.py` entry points built on a shared
OpenAPI-to-MCP-tool bridge in `mcp_servers/openapi_bridge.py`. That bridge
reads an OpenAPI spec JSON (`mcp_servers/specs/*.json`) plus a curated
"annotations" JSON (`mcp_servers/annotations/*.json` — usage notes, risk
level, tool category, pinned state, preconditions, related tools) and turns
each operation into an MCP `Tool`, with a discovery layer (`search_tools`,
`get_tool_schema`) rather than exposing hundreds of tools flat.

- **`cmp_admin_mcp`** — the main server. Merges multiple OpenAPI spec
  "domains" (`server`, `block_storage`, `network`, `server_v1`) onto one
  `Server` instance via `SpecSource`/`build_multi_spec_server`, plus:
  - `failure_pattern_matcher.py` — curated failure-pattern KB matching
    (`search_failure_patterns`), backed by
    `knowledge/failure_patterns.json`.
  - `investigation_reporting.py` — the ticket/plan/report tools
    (`start_investigate`, `submit_investigation_plan`,
    `submit_investigation_report`) that the `investigate-incident` skill
    drives.
  - `legacy_operations.py` — opt-in legacy operations (e.g.
    `get_volume_legacy`), gated behind
    `CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET=1`; off by default.
  - `resources.py` — MCP *resources* (not tools) for ground-truth lookups
    by id, e.g. `cmp://server-v1/{server_id}`,
    `cmp://elastic-ip/{elastic_ip_id}`. These never appear in `search_tools`
    — check `resources.py` before assuming a `get_x`-style tool doesn't
    exist.
  - Spec "domains" are not mutually exclusive: `server` and `server_v1`
    cover the same resource under different API versions and can both be
    enabled since their `operationId`s live in different namespaces.
  - Runtime-configurable via the admin GUI: spec sources, annotation
    overrides, and pinned-tool overrides are all layered over the built-in
    JSON defaults through `mcp_servers/shared/config_store.py`, which reads
    from the shared SQLite store (`mcp_servers/shared/db.py`,
    `data/guardian.db`). A server that never touches the GUI is unaffected
    — every config-store getter degrades to "nothing configured".
- **`cmp_logs_mcp`** — wraps Elasticsearch log search (`client.py`) with
  masking of sensitive fields (`mask.py`, backed by
  `mcp_servers/shared/masking.py`).
- **`cmp_notify_mcp`** — notification/ticketing integration
  (`mcp_servers/shared/tickets.py`).

Configuration for all servers is env-var based per `_ENV_PREFIX` (e.g.
`CMP_ADMIN_V2_BASE_URL`, `CMP_ADMIN_V2_PAT` or
`CMP_ADMIN_V2_USERNAME`/`CMP_ADMIN_V2_PASSWORD`), read once at server-build
time — these are ephemeral stdio subprocesses re-spawned per Claude Code
session, so there is deliberately no live-reload path.

`mcp_servers/prompts/assemble_log.py` registers an MCP *prompt* (not a
tool). MCP prompts in this environment surface only as a user-typed slash
command (`/mcp__cmp-admin__assemble_log`) — they are not callable
programmatically from a skill or from other code.

### Admin GUI (`admin_gui/`)

A local, single-operator, no-auth config UI for the MCP servers' runtime
state (spec sources, annotation overrides, pinned tools, masking patterns,
event log, tickets, failure patterns).

- `backend/app.py` — FastAPI app; `backend/main.py` — uvicorn entry point,
  binds `127.0.0.1:8765`.
- `backend/routers/` — one router per config surface (`config.py`,
  `events.py`, `failure_patterns.py`, `masking.py`, `servers.py`,
  `spec_sources.py`, `tickets.py`), all reading/writing the same shared
  SQLite store used by the MCP servers.
- `frontend/` — React 19 + TypeScript + Vite, using `@astryxdesign/*`
  design-system packages and `react-router-dom`. Linted with `oxlint`, not
  ESLint.

### Shared layer (`mcp_servers/shared/`)

- `db.py` — SQLite connection/schema for `data/guardian.db` (path
  overridable via `CMP_MCP_DATA_DIR`, isolated per-test by
  `tests/conftest.py`'s autouse fixture).
- `config_store.py` — typed getters/setters over that DB for connection
  config, spec sources, annotation/pinned overrides.
- `masking.py` — sensitive-field masking used by `cmp_logs_mcp`.
- `tickets.py` — ticket persistence used by `investigation_reporting.py`
  and `cmp_notify_mcp`.
- `telemetry.py` — event logging surfaced by the admin GUI's `events.py`
  router.

### The `investigate-incident` skill (`.claude/skills/investigate-incident/`)

Drives incident response using the `cmp-admin` MCP tools/resources: calls
`start_investigate` first (auto-attributes subsequent tool calls to a
ticket), gathers evidence via `search_logs` + `search_failure_patterns`
(never by trying to call the `assemble_log` prompt as a tool), reads
ground-truth via MCP resources, and reports back via
`submit_investigation_plan`/`submit_investigation_report`.
