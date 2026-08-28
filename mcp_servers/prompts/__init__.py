"""MCP Prompts for cmp-admin, registered directly on the same `Server`
instance as its tools and resources -- prompts have no backend of their own,
they only orchestrate tools cmp-admin/cmp-notify/cmp-logs already expose.

`register_prompts` is the one place `list_prompts`/`get_prompt` get wired up.
`@server.list_prompts()`/`@server.get_prompt()` each accept exactly one
handler per `Server` instance -- registering a second overwrites the first,
it does not add a second prompt -- so a future second prompt joins the
dispatch inside `_get_prompt` below, not a second registration call.
"""

from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

from mcp_servers.prompts import assemble_log
from mcp_servers.shared.telemetry import instrument_dispatch


def register_prompts(server: Server) -> None:
    @server.list_prompts()
    async def _list_prompts() -> list[types.Prompt]:
        return [assemble_log.build_prompt()]

    @server.get_prompt()
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        async def _dispatch() -> types.GetPromptResult:
            if name == assemble_log.NAME:
                return assemble_log.render(arguments or {})
            raise ValueError(f"Unknown prompt: {name}")

        return await instrument_dispatch(
            server=server.name,
            kind="prompt",
            name=name,
            arguments=arguments or {},
            source=f"{server.name}.prompt.{name}",
            dispatch=_dispatch,
        )


__all__ = ["register_prompts"]
