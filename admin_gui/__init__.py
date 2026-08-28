"""Local web GUI for visualizing and configuring the MCP servers under
`mcp_servers/` -- tool/resource/prompt catalogs, the call-event log, and
config (masking patterns, connection settings, spec-source selection,
pinned tools, the failure-pattern knowledge base).

Import direction is one-way: this package imports from `mcp_servers/*`
(the builder functions, `mcp_servers/shared/*`), never the other way around
-- the three MCP servers must work identically whether or not this package
is ever installed or run.

Binds to 127.0.0.1 only, no authentication -- a local, single-operator tool.
"""
