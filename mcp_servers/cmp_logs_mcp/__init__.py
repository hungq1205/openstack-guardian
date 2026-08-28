"""MCP server exposing Elasticsearch log search for CMP/OpenStack incidents.

Standalone, like every server under `mcp_servers/`: no dependency on the rest
of this repo's `core`/`platform`/`tools`/`integrations` tree, just `httpx`,
`mcp`, and the standard library. Run with `uv run python -m
mcp_servers.cmp_logs_mcp.main`.
"""
