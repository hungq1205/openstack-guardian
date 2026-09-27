# cmp-mcp

The CMP-level MCP in Guardian's multi-MCP controller: two MCP servers
(`cmp-admin`, `cmp-logs`) exposing a CMP (cloud management platform) admin
API and its Elasticsearch logs as tools, plus the `investigate-incident`
skill that drives incident response over them. See the top-level
[`../CLAUDE.md`](../CLAUDE.md) for how this fits alongside
`guardian-platform`/`MCP-OpenStack-Ops`, and this project's own
[`CLAUDE.md`](CLAUDE.md) for full architecture detail. This file is just the
"get it running" quick start.

## Install

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
```

This project depends on `guardian-platform` (the shared config/ticketing
library) via an editable path dependency, so `../guardian-platform` needs to
exist alongside this directory -- `uv sync` resolves it automatically, no
separate install step needed there.

## Starting the MCP servers

You normally don't run these by hand: Claude Code spawns them automatically
from `.mcp.json` once they're enabled (via the admin GUI's "MCP Servers"
page in `guardian-platform` -- see its own
[README](../guardian-platform/README.md)). To run either standalone (for
debugging its stdio protocol directly):

```bash
uv run python -m mcp_servers.cmp_admin_mcp.main
uv run python -m mcp_servers.cmp_logs_mcp.main
```

Both read their connection config (base URL, credentials) from environment
variables with a per-server prefix (e.g. `CMP_ADMIN_V2_BASE_URL`,
`CMP_ADMIN_V2_PAT`, `CMP_LOGS_ES_URL`, `CMP_LOGS_ES_INDEX`) -- set via the
admin GUI's MCP Servers page (stored in the shared `guardian.db`, synced
into `.mcp.json`'s `env` block for you), not a local `.env` file here.

## Running against the mock stack (no real CMP deployment needed)

For manual/interactive testing without hitting a real CMP admin API or
Elasticsearch cluster:

```bash
# Mock CMP admin API (see mock/cmp_server/README.md)
python mock/cmp_server/server.py            # :8081

# Mock Elasticsearch + Kibana (see mock/elasticsearch/README.md)
cd mock/elasticsearch && docker compose up -d
python seed.py
```

Then point the real servers at them:

```bash
export CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081
export CMP_LOGS_ES_URL=http://localhost:9200
export CMP_LOGS_ES_INDEX=iaas-api-2026.08.23
```

(The automated test suite never needs any of this -- it mocks HTTP directly
with `httpx.MockTransport`.)

## Tests / lint

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

See [`CLAUDE.md`](CLAUDE.md) for the OpenAPI-to-MCP-tool bridge, spec
domains, and the `investigate-incident` skill's CMP-level vs core-level
split.
