"""Entrypoint for the cmp-logs MCP server.

Configure via env vars: CMP_LOGS_ES_URL, CMP_LOGS_ES_INDEX, plus either
CMP_LOGS_ES_API_KEY or CMP_LOGS_ES_USERNAME/CMP_LOGS_ES_PASSWORD. Run with
`uv run python -m mcp_servers.cmp_logs_mcp.main`.
"""

from __future__ import annotations

from mcp_servers.cmp_logs_mcp.server import build_server
from mcp_servers.openapi_bridge import run_stdio


def main() -> None:
    run_stdio(build_server())


if __name__ == "__main__":
    main()
