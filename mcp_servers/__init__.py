"""Standalone MCP servers for CMP admin APIs, hosted inside the opensre repo.

OpenSRE itself is an MCP *client* (see integrations/openclaw/) -- it never
hosts a server. These do. They're kept in their own top-level package,
separate from integrations/ and tools/, because their role (exposing an
external API over MCP) is different from an OpenSRE-native tool or vendor
integration.
"""
