"""Cross-cutting storage/config/masking/telemetry code shared by every MCP
server under `mcp_servers/` *and* the `admin_gui` web GUI.

Import direction is one-way: `mcp_servers/*` and `admin_gui/*` both import
from here, this package never imports from either of them. The three MCP
servers must work exactly as they do today even if `admin_gui` is never
installed or run -- everything here degrades to "nothing configured" when
the shared SQLite store hasn't been touched yet.
"""
