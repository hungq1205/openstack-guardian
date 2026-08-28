"""MCP server for notifying a human admin when no automated fix applies.

Standalone, like every server under `mcp_servers/`: no dependency on the CMP
admin API or any other server here -- a real outbound notification is a
different capability entirely (Elasticsearch is another example: `cmp_logs_mcp`).
Run with `uv run python -m mcp_servers.cmp_notify_mcp.main`.
"""
