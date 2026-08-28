"""Hand-rolled MCP server for the single `notify_admin` operation.

Not built on `mcp_servers.openapi_bridge`'s OpenAPI-to-MCP machinery -- there
is no spec for an outbound webhook, and one hardcoded operation doesn't need
that generality (same reasoning as `cmp_logs_mcp`).
"""

from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from mcp_servers.cmp_notify_mcp.client import NotifyClient
from mcp_servers.shared.telemetry import instrument_dispatch

_TOOL_NAME = "notify_admin"
_TOOL_DESCRIPTION = (
    "Escalate to a human admin when no automated fix applies: a diagnosed "
    "root cause exists, but resolving it requires human judgment (a "
    "capacity-planning decision, a customer-facing choice, a request-"
    "construction bug) rather than an infrastructure state change. Not "
    "read-only, but not destructive either -- it sends a message, it "
    "doesn't touch any CMP or core resource."
)
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server_id": {
            "type": "string",
            "description": "The server or resource id this incident concerns.",
        },
        "root_cause": {
            "type": "string",
            "description": "The diagnosed root cause -- stated plainly, not a guess dressed up as certainty.",
        },
        "reasoning": {
            "type": "string",
            "description": "Why this needs a human rather than an automated fix.",
        },
        "evidence": {
            "type": "string",
            "description": "Optional supporting evidence (log lines, probe results) backing the root cause.",
        },
    },
    "required": ["server_id", "root_cause", "reasoning"],
}


def build_server() -> Server:
    client = NotifyClient.from_env()
    server: Server = Server("cmp-notify")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=_TOOL_NAME,
                description=_TOOL_DESCRIPTION,
                inputSchema=_INPUT_SCHEMA,
                annotations=types.ToolAnnotations(
                    readOnlyHint=False, destructiveHint=False, idempotentHint=False
                ),
            )
        ]

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def _dispatch() -> dict[str, Any]:
            if tool_name != _TOOL_NAME:
                return {"error": "unknown_tool", "tool": tool_name}
            return client.send(
                server_id=arguments["server_id"],
                root_cause=arguments["root_cause"],
                reasoning=arguments["reasoning"],
                evidence=arguments.get("evidence"),
            )

        return await instrument_dispatch(
            server=server.name,
            kind="tool",
            name=tool_name,
            arguments=arguments,
            source=f"{server.name}.tool.{tool_name}",
            dispatch=_dispatch,
            action=f"POST {client.webhook_url}" if client.webhook_url else None,
            requires_approval=tool_name == _TOOL_NAME,
        )

    return server


__all__ = ["build_server"]
