"""Entrypoint for the cmp-notify MCP server.

Configure via CMP_NOTIFY_WEBHOOK_URL. Run with `uv run python -m
mcp_servers.cmp_notify_mcp.main`.
"""

from __future__ import annotations

from mcp_servers.cmp_notify_mcp.server import build_server
from mcp_servers.openapi_bridge import run_stdio


def main() -> None:
    run_stdio(build_server())


if __name__ == "__main__":
    main()
