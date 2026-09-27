"""Re-export shim -- the real masking logic now lives in
`guardian_platform.masking`, shared with the telemetry logging layer used
by all three MCP servers, not just cmp-logs. Kept here so existing imports
(`from mcp_servers.cmp_logs_mcp.mask import mask_text, mask_value`) keep
working unchanged.
"""

from __future__ import annotations

from guardian_platform.masking import mask_text, mask_value

__all__ = ["mask_text", "mask_value"]
