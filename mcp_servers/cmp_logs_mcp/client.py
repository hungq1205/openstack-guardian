"""Real Elasticsearch HTTP client for CMP/core log search, configured from
environment variables.

Raw REST calls via `httpx` (matching `CmpApiClient`'s pattern in
`openapi_bridge.py`) rather than a dedicated `elasticsearch` package -- one
fewer dependency for a handful of query shapes.

Both public search methods (`search`, `follow_request_id`) target the same
gateway-log index -- see `mock/elasticsearch/README.md` for the concrete
field schema a real (or mock) index needs. There is no `server_id`/`task_id`
field anywhere on this index; a future celery-worker-log source would need
its own index/config.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_servers.cmp_logs_mcp.mask import mask_text, mask_value
from mcp_servers.shared.config_store import get_config_value

_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_MAX_RESULTS = 100
_DEFAULT_CORRELATION_MAX_RESULTS = 200
_MAX_MAX_RESULTS = 1000

_KNOWN_NOISE_TEMPLATES = (
    "neutronclient_deprecation",
    "matplotlib_cache_notice",
    "uwsgi_lifecycle",
)

# Field names on the gateway-log index -- see mock/elasticsearch/seed.py for
# how a mock index is populated with exactly these fields.
_TIMESTAMP_FIELD = "@timestamp"
_MESSAGE_FIELD = "message"
_RAW_LOG_FIELD = "raw_log"
_LEVEL_FIELD = "level"
_LOGGER_FIELD = "logger"
_GATEWAY_REQUEST_ID_FIELD = "request_id"
_HOSTNAME_FIELD = "hostname"
_METHOD_FIELD = "method"
_PATH_FIELD = "path"
_STATUS_FIELD = "status"
_DURATION_MS_FIELD = "duration_ms"
_USERNAME_FIELD = "username"
_CLIENT_IP_FIELD = "client_ip"
_NOISE_TEMPLATE_FIELD = "noise_template"


@dataclass
class ElasticsearchLogsClient:
    """Configured from `CMP_LOGS_ES_*` environment variables."""

    base_url: str
    index: str
    auth_header: dict[str, str]

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> ElasticsearchLogsClient:
        """Build directly from an already-collected fields dict (`url`,
        `index`, `api_key` or `username`/`password`) -- e.g. the admin GUI's
        connection-test endpoint, checking a not-yet-saved form value.
        `from_env` is just this plus env-var/config-store lookup."""
        base_url = str(fields.get("url") or "").rstrip("/")
        index = str(fields.get("index") or "")
        api_key = fields.get("api_key")
        username = fields.get("username")
        password = fields.get("password")
        auth_header: dict[str, str] = {}
        if api_key:
            auth_header["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth_header["Authorization"] = f"Basic {credentials}"
        return cls(base_url=base_url, index=index, auth_header=auth_header)

    @classmethod
    def from_env(cls) -> ElasticsearchLogsClient:
        """Env var wins if set; otherwise falls back to the admin GUI's config
        store (see `shared.config_store`), then to an empty default -- a
        server run without ever touching the GUI is unaffected."""
        return cls.from_fields(
            {
                "url": os.environ.get("CMP_LOGS_ES_URL") or get_config_value("CMP_LOGS_ES", "url"),
                "index": os.environ.get("CMP_LOGS_ES_INDEX")
                or get_config_value("CMP_LOGS_ES", "index"),
                "api_key": os.environ.get("CMP_LOGS_ES_API_KEY")
                or get_config_value("CMP_LOGS_ES", "api_key"),
                "username": os.environ.get("CMP_LOGS_ES_USERNAME")
                or get_config_value("CMP_LOGS_ES", "username"),
                "password": os.environ.get("CMP_LOGS_ES_PASSWORD")
                or get_config_value("CMP_LOGS_ES", "password"),
            }
        )

    def check_connection(self, *, client: httpx.Client | None = None) -> dict[str, Any]:
        """A cheap, safe connectivity probe (`_count`, not a real search) for
        the admin GUI's 'test connection' button. Never raises."""
        if not self.base_url or not self.index:
            return {"reachable": False, "message": "no Elasticsearch URL/index configured"}
        url = f"{self.base_url}/{self.index}/_count"
        owns_client = client is None
        http_client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        try:
            response = http_client.get(url, headers=self.auth_header)
            if response.status_code >= 400:
                return {
                    "reachable": False,
                    "status_code": response.status_code,
                    "message": mask_text(response.text, source="cmp-logs.test_connection"),
                }
            return {"reachable": True, "status_code": response.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "message": str(exc)}
        finally:
            if owns_client:
                http_client.close()

    def _execute_search(
        self, *, tool_name: str, query_body: dict[str, Any], client: httpx.Client | None = None
    ) -> dict[str, Any]:
        """POST `query_body` to `{base_url}/{index}/_search`, returning the
        raw decoded ES response or a masked `{"error": ...}` envelope. Never
        raises. Shared by every public search method -- callers map `hits`
        into their own output shape and mask that mapped shape themselves;
        this owns only the HTTP/error handling common to all of them.
        """
        if not self.base_url or not self.index:
            return {
                "error": "not_configured",
                "message": f"{tool_name}: no Elasticsearch URL/index configured",
            }
        url = f"{self.base_url}/{self.index}/_search"
        owns_client = client is None
        http_client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        try:
            response = http_client.post(
                url,
                json=query_body,
                headers={"Content-Type": "application/json", **self.auth_header},
            )
            try:
                data: Any = response.json()
            except ValueError:
                return {
                    "error": "invalid_response",
                    "message": mask_text(response.text, source=f"cmp-logs.{tool_name}"),
                }
            if response.status_code >= 400:
                return {
                    "error": "request_failed",
                    "status_code": response.status_code,
                    "message": mask_value(data, source=f"cmp-logs.{tool_name}"),
                }
            return data
        except httpx.HTTPError as exc:
            return {"error": "request_failed", "message": str(exc)}
        finally:
            if owns_client:
                http_client.close()

    def search(
        self,
        *,
        query: str | None = None,
        level: str | None = None,
        exclude_level: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        method: str | None = None,
        status: int | None = None,
        path_prefix: str | None = None,
        hostname: str | None = None,
        since: str | None = None,
        until: str | None = None,
        exclude_known_noise: bool = False,
        max_results: int = _DEFAULT_MAX_RESULTS,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """General-purpose log search: an optional free-text phrase match
        (`query`, over `message`/`raw_log`) combined freely with any number
        of structured field filters and a time range -- e.g. "ERROR entries
        on iaas-2 in this window" (`level` + `hostname` + `since`/`until`,
        no text needed), "other 404s on /api/v1/vbs" (`path_prefix` +
        `status`), or "anything but INFO, excluding known noise"
        (`exclude_level="INFO"` + `exclude_known_noise=True`). At least one
        of query/level/exclude_level/logger/request_id/method/status/
        path_prefix/hostname/since/until must be given, to avoid an
        unbounded scan of the whole index. Never raises.

        Every string in the response is masked (`mask.mask_value`) before
        returning, unconditionally -- there is no unmasked path a caller
        could opt into.
        """
        if not any(
            [
                query,
                level,
                exclude_level,
                logger,
                request_id,
                method,
                status is not None,
                path_prefix,
                hostname,
                since,
                until,
            ]
        ):
            return {
                "error": "invalid_arguments",
                "message": (
                    "search_logs requires at least one of query/level/exclude_level/logger/"
                    "request_id/method/status/path_prefix/hostname/since/until"
                ),
            }
        filters: list[dict[str, Any]] = []
        if level:
            filters.append({"term": {_LEVEL_FIELD: level}})
        if exclude_level:
            filters.append({"bool": {"must_not": [{"term": {_LEVEL_FIELD: exclude_level}}]}})
        if logger:
            filters.append({"term": {_LOGGER_FIELD: logger}})
        if request_id:
            filters.append({"term": {_GATEWAY_REQUEST_ID_FIELD: request_id}})
        if method:
            filters.append({"term": {_METHOD_FIELD: method.upper()}})
        if status is not None:
            filters.append({"term": {_STATUS_FIELD: status}})
        if path_prefix:
            filters.append({"prefix": {_PATH_FIELD: path_prefix}})
        if hostname:
            filters.append({"term": {_HOSTNAME_FIELD: hostname}})
        _add_time_range_filter(filters, since=since, until=until)
        if exclude_known_noise:
            filters.append(
                {
                    "bool": {
                        "must_not": [{"terms": {_NOISE_TEMPLATE_FIELD: list(_KNOWN_NOISE_TEMPLATES)}}]
                    }
                }
            )
        bool_query: dict[str, Any] = {"filter": filters}
        if query:
            bool_query["must"] = [
                {
                    "multi_match": {
                        "query": query,
                        "fields": [_MESSAGE_FIELD, _RAW_LOG_FIELD],
                        "type": "phrase",
                    }
                }
            ]
        query_body = {
            "query": {"bool": bool_query},
            "sort": [{_TIMESTAMP_FIELD: "asc"}],
            "size": min(max_results, _MAX_MAX_RESULTS),
        }
        data = self._execute_search(tool_name="search_logs", query_body=query_body, client=client)
        if "error" in data:
            return data
        return {"logs": mask_value(_extract_gateway_hits(data), source="cmp-logs.search_logs")}

    def follow_request_id(
        self,
        *,
        request_id: str,
        max_results: int = _DEFAULT_CORRELATION_MAX_RESULTS,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """Every structured log entry tagged with this gateway request-id,
        oldest first -- reconstructs one incident's cascade across levels.
        Raw stderr/uWSGI access lines and Python tracebacks are never tagged
        with a request-id at the source, so they won't appear here even for
        the same incident; use search(exclude_level="INFO", since=..., until=...)
        over the same time window for those. Never raises.
        """
        if not request_id:
            return {
                "error": "invalid_arguments",
                "message": "follow_request_id: request_id is required",
            }
        query_body = {
            "query": {"term": {_GATEWAY_REQUEST_ID_FIELD: request_id}},
            "sort": [{_TIMESTAMP_FIELD: "asc"}],
            "size": min(max_results, _MAX_MAX_RESULTS),
        }
        data = self._execute_search(tool_name="follow_request_id", query_body=query_body, client=client)
        if "error" in data:
            return data
        return {"entries": mask_value(_extract_gateway_hits(data), source="cmp-logs.follow_request_id")}


def _add_time_range_filter(filters: list[dict[str, Any]], *, since: str | None, until: str | None) -> None:
    if not since and not until:
        return
    bounds: dict[str, str] = {}
    if since:
        bounds["gte"] = since
    if until:
        bounds["lte"] = until
    filters.append({"range": {_TIMESTAMP_FIELD: bounds}})


def _extract_gateway_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    hits = data.get("hits", {}).get("hits", [])
    return [
        {
            "id": hit.get("_id"),
            "timestamp": hit.get("_source", {}).get(_TIMESTAMP_FIELD),
            "level": hit.get("_source", {}).get(_LEVEL_FIELD),
            "logger": hit.get("_source", {}).get(_LOGGER_FIELD),
            "request_id": hit.get("_source", {}).get(_GATEWAY_REQUEST_ID_FIELD),
            "hostname": hit.get("_source", {}).get(_HOSTNAME_FIELD),
            "message": hit.get("_source", {}).get(_MESSAGE_FIELD),
            "method": hit.get("_source", {}).get(_METHOD_FIELD),
            "path": hit.get("_source", {}).get(_PATH_FIELD),
            "status": hit.get("_source", {}).get(_STATUS_FIELD),
            "duration_ms": hit.get("_source", {}).get(_DURATION_MS_FIELD),
            "username": hit.get("_source", {}).get(_USERNAME_FIELD),
            "client_ip": hit.get("_source", {}).get(_CLIENT_IP_FIELD),
            "noise_template": hit.get("_source", {}).get(_NOISE_TEMPLATE_FIELD),
            "raw": hit.get("_source", {}),
        }
        for hit in hits
    ]


__all__ = ["ElasticsearchLogsClient"]
