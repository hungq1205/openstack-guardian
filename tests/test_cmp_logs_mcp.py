"""Tests for the cmp-logs MCP server: request construction, PII masking, and
the honest `not_configured` response -- the same real-request-shape strategy
`test_openapi_bridge.py` already uses (`httpx.MockTransport`), since there is
no real Elasticsearch cluster to test against here either.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from guardian_platform.config_store import set_tool_annotation_override

from mcp_servers.cmp_logs_mcp.client import ElasticsearchLogsClient
from mcp_servers.cmp_logs_mcp.mask import mask_text, mask_value
from mcp_servers.cmp_logs_mcp.server import build_server

_ALL_TOOL_NAMES = ["search_logs", "follow_request_id"]


def _client_with_transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _refuse_transport(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("transport should not have been called -- a guardrail should have short-circuited first")


def _hits(*sources: dict[str, Any]) -> dict[str, Any]:
    return {"hits": {"hits": [{"_id": str(i), "_source": source} for i, source in enumerate(sources)]}}


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.read())


# ---- search_logs (redesigned: free-text over message/raw_log) --------------


def test_search_with_no_arguments_at_all_short_circuits() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    result = client.search(client=_client_with_transport(_refuse_transport))

    assert result["error"] == "invalid_arguments"


def test_search_with_only_a_structured_filter_and_no_query_works() -> None:
    """Confirms `query` is now optional -- a filters-only call (no free text)
    is the whole point of generalizing search_logs beyond text search."""
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(level="ERROR", hostname="iaas-2", client=_client_with_transport(handler))

    body = _body(captured["request"])
    assert "must" not in body["query"]["bool"]
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"level": "ERROR"}} in filters
    assert {"term": {"hostname": "iaas-2"}} in filters


def test_search_without_configuration_reports_not_configured() -> None:
    client = ElasticsearchLogsClient(base_url="", index="", auth_header={})

    result = client.search(query="98fc04db-66cd-4b62-a69e-a1c6f81a0aac")

    assert result == {
        "error": "not_configured",
        "message": "search_logs: no Elasticsearch URL/index configured",
    }


def test_search_sends_a_phrase_query_across_message_and_raw_log() -> None:
    client = ElasticsearchLogsClient(
        base_url="https://es.example.com",
        index="iaas-api-2026.08.23",
        auth_header={"Authorization": "ApiKey secret"},
    )
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(
        query="cinderclient.exceptions.NotFound",
        since="2026-08-23T23:00:00",
        until="2026-08-23T23:30:00",
        hostname="iaas-2",
        client=_client_with_transport(handler),
    )

    request = captured["request"]
    assert request.url == "https://es.example.com/iaas-api-2026.08.23/_search"
    assert request.headers["Authorization"] == "ApiKey secret"
    body = _body(request)
    must = body["query"]["bool"]["must"][0]["multi_match"]
    assert must == {"query": "cinderclient.exceptions.NotFound", "fields": ["message", "raw_log"], "type": "phrase"}
    filters = body["query"]["bool"]["filter"]
    assert {"range": {"@timestamp": {"gte": "2026-08-23T23:00:00", "lte": "2026-08-23T23:30:00"}}} in filters
    assert {"term": {"hostname": "iaas-2"}} in filters


def test_search_defaults_max_results_to_100_and_clamps_to_1000() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(query="x", client=_client_with_transport(handler))
    assert _body(captured["request"])["size"] == 100

    client.search(query="x", max_results=5000, client=_client_with_transport(handler))
    assert _body(captured["request"])["size"] == 1000


def test_search_masks_pii_in_returned_log_fields() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hits(
                {
                    "@timestamp": "2026-08-23T00:00:00Z",
                    "message": "build failed, notify jane.doe@example.com at 10.0.0.5",
                }
            ),
        )

    result = client.search(query="build failed", client=_client_with_transport(handler))

    message = result["logs"][0]["message"]
    assert "jane.doe@example.com" not in message
    assert "10.0.0.5" not in message
    assert "[REDACTED:EMAIL]" in message
    assert "[REDACTED:IP" in message


def test_search_reports_upstream_errors_with_masking_still_applied() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "contact admin@example.com"})

    result = client.search(query="x", client=_client_with_transport(handler))

    assert result["error"] == "request_failed"
    assert result["status_code"] == 500
    assert "admin@example.com" not in str(result["message"])


def test_search_masks_a_non_json_upstream_response_too() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="upstream said: contact ops@example.com")

    result = client.search(query="x", client=_client_with_transport(handler))

    assert result["error"] == "invalid_response"
    assert "ops@example.com" not in result["message"]
    assert "[REDACTED:EMAIL]" in result["message"]


# ---- follow_request_id ------------------------------------------------------


def test_follow_request_id_without_id_short_circuits() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    result = client.follow_request_id(request_id="", client=_client_with_transport(_refuse_transport))

    assert result == {"error": "invalid_arguments", "message": "follow_request_id: request_id is required"}


def test_follow_request_id_sends_a_term_query() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.follow_request_id(
        request_id="df3daa616eb14781866673d4c67d062e", client=_client_with_transport(handler)
    )

    body = _body(captured["request"])
    assert body["query"] == {"term": {"request_id": "df3daa616eb14781866673d4c67d062e"}}
    assert body["sort"] == [{"@timestamp": "asc"}]


def test_follow_request_id_masks_and_returns_entries() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hits(
                {
                    "@timestamp": "2026-08-23T23:26:37.521",
                    "level": "WARNING",
                    "logger": "django.request",
                    "request_id": "df3daa616eb14781866673d4c67d062e",
                    "hostname": "iaas-2",
                    "message": "Not Found: /api/v1/vbs/volumes/98fc04db-66cd-4b62-a69e-a1c6f81a0aac/",
                    "client_ip": "10.208.198.13",
                }
            ),
        )

    result = client.follow_request_id(
        request_id="df3daa616eb14781866673d4c67d062e", client=_client_with_transport(handler)
    )

    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["level"] == "WARNING"
    assert "[REDACTED:IP" in entry["client_ip"]


# ---- search: structured filters (folded in from the old filter_access_logs) -


def test_search_maps_every_structured_filter() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(
        logger="log.response",
        path_prefix="/api/v1/vbs",
        method="get",
        status=404,
        hostname="iaas-2",
        since="2026-08-23T23:00:00",
        until="2026-08-23T23:30:00",
        client=_client_with_transport(handler),
    )

    filters = _body(captured["request"])["query"]["bool"]["filter"]
    assert {"term": {"logger": "log.response"}} in filters
    assert {"prefix": {"path": "/api/v1/vbs"}} in filters
    assert {"term": {"method": "GET"}} in filters
    assert {"term": {"status": 404}} in filters
    assert {"term": {"hostname": "iaas-2"}} in filters
    assert {"range": {"@timestamp": {"gte": "2026-08-23T23:00:00", "lte": "2026-08-23T23:30:00"}}} in filters


def test_search_masks_client_ip_on_a_structured_filter_hit() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hits(
                {
                    "@timestamp": "2026-08-23T23:26:37.521",
                    "logger": "log.response",
                    "status": 404,
                    "client_ip": "10.208.198.13",
                }
            ),
        )

    result = client.search(status=404, client=_client_with_transport(handler))

    assert "[REDACTED:IP" in result["logs"][0]["client_ip"]


# ---- search: exclude_level/exclude_known_noise (folded in from find_anomalies)


def test_search_exclude_level_and_known_noise_map_to_must_not_filters() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(
        exclude_level="INFO",
        exclude_known_noise=True,
        since="2026-08-23T23:26:00",
        until="2026-08-23T23:27:00",
        client=_client_with_transport(handler),
    )

    filters = _body(captured["request"])["query"]["bool"]["filter"]
    assert {"range": {"@timestamp": {"gte": "2026-08-23T23:26:00", "lte": "2026-08-23T23:27:00"}}} in filters
    assert {"bool": {"must_not": [{"term": {"level": "INFO"}}]}} in filters
    assert {
        "bool": {
            "must_not": [
                {
                    "terms": {
                        "noise_template": [
                            "neutronclient_deprecation",
                            "matplotlib_cache_notice",
                            "uwsgi_lifecycle",
                        ]
                    }
                }
            ]
        }
    } in filters


def test_search_exclude_known_noise_defaults_to_off() -> None:
    """Unlike the old find_anomalies (noise-exclusion on by default), the
    generalized search defaults it off -- a caller doing a plain text/field
    search shouldn't have results silently dropped unless they ask for it."""
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(exclude_level="INFO", since="x", until="y", client=_client_with_transport(handler))

    filters = _body(captured["request"])["query"]["bool"]["filter"]
    assert not any("noise_template" in json.dumps(f) for f in filters)


def test_search_masks_returned_entries_on_an_exclude_level_query() -> None:
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hits(
                {
                    "@timestamp": "2026-08-23T23:26:37.519",
                    "level": "ERROR",
                    "logger": "vblockstorage.views.volume_views",
                    "message": (
                        "Volume 98fc04db-66cd-4b62-a69e-a1c6f81a0aac could not be found, "
                        "contact ops@example.com"
                    ),
                }
            ),
        )

    result = client.search(
        exclude_level="INFO",
        since="2026-08-23T23:26:00",
        until="2026-08-23T23:27:00",
        client=_client_with_transport(handler),
    )

    assert "[REDACTED:EMAIL]" in result["logs"][0]["message"]


def test_search_combines_free_text_query_with_structured_filters() -> None:
    """The whole point of folding everything into one tool: text + filters
    together in a single call, which none of the old separate tools could do."""
    client = ElasticsearchLogsClient(base_url="https://es.example.com", index="cmp-logs", auth_header={})
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"hits": {"hits": []}})

    client.search(
        query="NotFound",
        level="ERROR",
        hostname="iaas-2",
        client=_client_with_transport(handler),
    )

    body = _body(captured["request"])
    assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == "NotFound"
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"level": "ERROR"}} in filters
    assert {"term": {"hostname": "iaas-2"}} in filters


# ---- shared masking helpers (unchanged) -------------------------------------


@pytest.mark.parametrize(
    "text,expected_kind",
    [
        ("reach me at test.user@example.com", "EMAIL"),
        ("connect to 192.168.1.20 for details", "IPV4"),
        ("call 555-123-4567 if urgent", "PHONE"),
    ],
)
def test_mask_text_redacts_each_pii_kind(text: str, expected_kind: str) -> None:
    assert f"[REDACTED:{expected_kind}]" in mask_text(text)


def test_mask_value_recurses_through_nested_structures() -> None:
    value = {"a": ["reach test@example.com", {"b": "10.0.0.1"}]}

    masked = mask_value(value)

    assert masked == {"a": ["reach [REDACTED:EMAIL]", {"b": "[REDACTED:IPV4]"}]}


# ---- MCP-level wiring --------------------------------------------------------


def test_build_server_exposes_two_read_only_tools() -> None:
    server = build_server()
    assert server.name == "cmp-logs"


@pytest.mark.asyncio
async def test_list_tools_declares_both_tools_as_read_only() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    assert [tool.name for tool in result.tools] == _ALL_TOOL_NAMES
    for tool in result.tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_hidden_override_no_longer_affects_list_tools() -> None:
    """2026-09-26: this server is piped through `guardian-admin` as a proxied
    external connection now -- enable/disable is the unified tool registry's
    job, applied one layer up by `guardian_platform.admin_mcp.proxy`, not by
    this server's own `list_tools()`. The old curated `hidden` field has no
    effect here anymore (unlike before): both tools always list."""
    from mcp.shared.memory import create_connected_server_and_client_session

    set_tool_annotation_override("cmp-logs", "follow_request_id", {"hidden": True})

    server = build_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    names = {tool.name for tool in result.tools}
    assert names == {"search_logs", "follow_request_id"}


@pytest.mark.asyncio
async def test_real_round_trip_through_the_live_mcp_subprocess() -> None:
    """The same live-subprocess proof every other server here uses: genuinely
    spawn `python -m mcp_servers.cmp_logs_mcp.main` (the exact command
    `.mcp.json` runs) and get a real response back -- not the in-process
    session the other tests here use."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_servers.cmp_logs_mcp.main"])
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool("search_logs", {"query": "srv-1"})

    assert result.structuredContent == {
        "error": "not_configured",
        "message": "search_logs: no Elasticsearch URL/index configured",
    }
