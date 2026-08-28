"""Real webhook client for admin notifications, configured from environment variables.

A generic JSON-POST webhook rather than a specific vendor (Slack/Telegram/a
ticketing system) -- most of those accept a plain webhook URL, and picking
one vendor now would be guessing at an integration nobody has asked for yet.
Swapping this for a vendor-specific client later only touches this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from mcp_servers.shared.config_store import get_config_value

_DEFAULT_TIMEOUT_SECONDS = 15


@dataclass
class NotifyClient:
    """Configured from `CMP_NOTIFY_WEBHOOK_URL`."""

    webhook_url: str

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> NotifyClient:
        """Build directly from an already-collected fields dict -- e.g. the
        admin GUI's connection-test endpoint, checking a not-yet-saved form
        value. `from_env` is just this plus env-var/config-store lookup."""
        return cls(webhook_url=str(fields.get("webhook_url") or "").rstrip())

    @classmethod
    def from_env(cls) -> NotifyClient:
        """Env var wins if set; otherwise falls back to the admin GUI's config
        store (see `shared.config_store`), then to an empty default -- a
        server run without ever touching the GUI is unaffected."""
        webhook_url = os.environ.get("CMP_NOTIFY_WEBHOOK_URL") or get_config_value(
            "CMP_NOTIFY", "webhook_url"
        )
        return cls.from_fields({"webhook_url": webhook_url})

    def send(
        self,
        *,
        server_id: str,
        root_cause: str,
        reasoning: str,
        evidence: str | None = None,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """POST an admin notification. Never raises -- a request failure or a
        missing webhook URL both come back as a structured `{"error": ...}` dict,
        the same convention every other client in this project follows."""
        if not self.webhook_url:
            return {
                "error": "not_configured",
                "message": "notify_admin: no webhook URL configured",
            }
        payload: dict[str, Any] = {
            "server_id": server_id,
            "root_cause": root_cause,
            "reasoning": reasoning,
        }
        if evidence:
            payload["evidence"] = evidence
        owns_client = client is None
        http_client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        try:
            response = http_client.post(self.webhook_url, json=payload)
            return {
                "notified": response.status_code < 400,
                "status_code": response.status_code,
            }
        except httpx.HTTPError as exc:
            return {"error": "request_failed", "message": str(exc)}
        finally:
            if owns_client:
                http_client.close()

    def send_test(self, *, client: httpx.Client | None = None) -> dict[str, Any]:
        """POST a distinctly-shaped test payload -- never `send()`'s real
        incident-report shape -- for the admin GUI's 'test connection'
        button. Never raises."""
        if not self.webhook_url:
            return {"reachable": False, "message": "notify_admin: no webhook URL configured"}
        payload = {
            "test": True,
            "message": "cmp-mcp admin GUI connectivity test",
            "sent_at": datetime.now(UTC).isoformat(),
        }
        owns_client = client is None
        http_client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        try:
            response = http_client.post(self.webhook_url, json=payload)
            return {"reachable": response.status_code < 400, "status_code": response.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "message": str(exc)}
        finally:
            if owns_client:
                http_client.close()


__all__ = ["NotifyClient"]
