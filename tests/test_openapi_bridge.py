"""Tests for the generic OpenAPI-to-MCP bridge, against the real CMP specs.

No real CMP host or credentials exist to test against, so this verifies what
actually is verifiable: parsing produces correct, complete operations for
all three specs, and `CmpApiClient.call` builds the exact real HTTP request
(method, URL with path params substituted, query string, JSON body, auth
header) that would be sent -- using `httpx.MockTransport` rather than a live
server. A genuine end-to-end round trip against a real CMP instance is not
possible without one being provided.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import jsonschema
import pytest

from mcp_servers.openapi_bridge import (
    CmpApiClient,
    _wrap_output_schema,
    build_input_schema,
    build_server,
    description_for,
    load_annotations,
    load_operations,
)

_SPECS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "specs"
_SERVER_SPEC = _SPECS_DIR / "server.json"
_SERVER_V1_SPEC = _SPECS_DIR / "server_v1.json"
_BLOCK_STORAGE_SPEC = _SPECS_DIR / "block_storage.json"
_NETWORK_SPEC = _SPECS_DIR / "network.json"

_ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "annotations"
_SERVER_ANNOTATIONS = _ANNOTATIONS_DIR / "server.json"
_BLOCK_STORAGE_ANNOTATIONS = _ANNOTATIONS_DIR / "block_storage.json"
_NETWORK_ANNOTATIONS = _ANNOTATIONS_DIR / "network.json"

# get_volume_legacy is hand-defined (see cmp_admin_mcp/legacy_operations.py)
# because no current-spec replacement for a plain volume GET has been found --
# unlike server's recreate_server (removed: no such v1 endpoint ever existed,
# rebuild_server has always been the only real one), this one stands in for a
# genuine gap in the current API, not a deprecated alternative to something
# that already exists.
_LEGACY_ANNOTATION_KEYS = {
    "server": frozenset(),
    "block_storage": frozenset({"get_volume_legacy"}),
    "network": frozenset(),
}


@pytest.mark.parametrize(
    "spec_path,min_operations",
    [(_SERVER_SPEC, 40), (_BLOCK_STORAGE_SPEC, 10), (_NETWORK_SPEC, 20)],
)
def test_load_operations_parses_every_real_spec(spec_path: Path, min_operations: int) -> None:
    operations = load_operations(spec_path)
    assert len(operations) >= min_operations
    for op in operations.values():
        assert op.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert op.path.startswith("/admin-v2/")


def test_server_spec_has_rebuild_server_operation() -> None:
    operations = load_operations(_SERVER_SPEC)
    op = operations["rebuild_server"]
    assert op.method == "POST"
    assert op.path == "/admin-v2/server/servers/{server_id}/rebuild/"
    assert [p.name for p in op.parameters] == ["server_id"]
    assert op.parameters[0].required is True


def test_rebuild_server_requires_project_id_in_body() -> None:
    """Regression: a live round-trip test caught this -- the earlier hand-written
    demo stub (integrations/cmp/tools/rebuild_server_tool) only knew about
    `server_id`, but the real endpoint's `ServerRebuildSchema` requires
    `project_id` too. This is exactly the class of gap the spec-driven bridge
    exists to catch automatically instead of relying on a human to notice.
    """
    operations = load_operations(_SERVER_SPEC)
    schema = build_input_schema(operations["rebuild_server"])
    assert "project_id" in schema["properties"]
    assert "project_id" in schema["required"]


def test_build_input_schema_flattens_request_body_fields() -> None:
    operations = load_operations(_BLOCK_STORAGE_SPEC)
    op = operations["create_backup_policy"]
    schema = build_input_schema(op)
    assert schema["type"] == "object"
    for field in ("name", "status", "region_id", "project_id", "retention"):
        assert field in schema["properties"], field
    assert set(schema["required"]) == {"name", "status", "region_id", "project_id"}


def test_build_input_schema_includes_path_params() -> None:
    operations = load_operations(_NETWORK_SPEC)
    op = operations["get_elastic_ip"]
    schema = build_input_schema(op)
    assert schema["properties"]["elastic_ip_id"]["format"] == "uuid"
    assert schema["required"] == ["elastic_ip_id"]


def test_load_operations_resolves_output_schema_for_a_normal_get() -> None:
    operations = load_operations(_SERVER_SPEC)
    schema = operations["get_compute_node"].output_schema
    assert schema is not None
    assert schema["type"] == "object"
    assert "hostname" in schema["properties"]
    assert "id" in schema["required"]


@pytest.mark.parametrize(
    "spec_path,operation_id",
    [
        (_SERVER_SPEC, "rebuild_server"),  # 202, no content
        (_SERVER_SPEC, "delete_aggregate"),  # 201, no content -- not even the usual 204
        (_SERVER_SPEC, "delete_server"),  # 202, no content
    ],
)
def test_bodyless_operations_have_no_output_schema(spec_path: Path, operation_id: str) -> None:
    operations = load_operations(spec_path)
    assert operations[operation_id].output_schema is None


def test_wrapped_output_schema_accepts_a_real_non_2xx_error_response() -> None:
    """Regression for a real bug found live 2026-09-27 (ticket #98): every `server_v1` operation
    declares at least one 4xx response, all `ErrorResponse`-shaped (e.g. `admin_api_servers_
    retrieve`'s 404, `{"detail": "Not found."}`) -- completely unlike its 2xx schema
    (`ServerAdminDetail`). `CmpApiClient.call` wraps a 404 in the exact same
    `{"status_code": ..., "data": ...}` envelope as a 200, so the wrapped outputSchema must accept
    `data` being error-shaped whenever `status_code` isn't 2xx -- before this fix, a completely
    valid 404 (the KB's own designed starting state for a never-created server) failed the SDK's
    output validation outright."""
    operations = load_operations(_SERVER_V1_SPEC)
    resolved = operations["admin_api_servers_retrieve"].output_schema
    assert resolved is not None
    wrapped = _wrap_output_schema(resolved)

    jsonschema.validate({"status_code": 404, "data": {"detail": "Not found."}}, wrapped)
    jsonschema.validate({"status_code": 400, "data": {"detail": {"id": ["not a valid uuid"]}}}, wrapped)


def test_wrapped_output_schema_still_rejects_a_malformed_2xx_response() -> None:
    """The fix above must not become "accept literally anything" -- a 2xx response still has to
    validate against the real success schema, so a broken 200 (missing required fields, the exact
    class of mock bug caught live the same day, tickets #94/#95) is still caught, not silently
    waved through just because *some* status code now gets a free pass on `data`'s shape."""
    operations = load_operations(_SERVER_V1_SPEC)
    resolved = operations["admin_api_servers_retrieve"].output_schema
    assert resolved is not None
    wrapped = _wrap_output_schema(resolved)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"status_code": 200, "data": {"id": "not-even-close-to-a-full-server"}}, wrapped)


def test_list_server_types_output_schema_has_no_fabricated_pagination_fields() -> None:
    """Pagedstr (list_server_types's response) is the one paged schema without
    next/previous, unlike every other Paged* schema -- the resolver must reflect
    the real spec, not a hand-authored 'every list is paged the same way' shape."""
    operations = load_operations(_SERVER_SPEC)
    schema = operations["list_server_types"].output_schema
    assert schema is not None
    assert set(schema["properties"]) == {"count", "results"}
    assert schema["properties"]["results"]["items"]["type"] == "string"


@pytest.mark.parametrize("spec_path", [_SERVER_SPEC, _BLOCK_STORAGE_SPEC, _NETWORK_SPEC])
def test_operations_with_paging_params_mention_pagination_in_description(spec_path: Path) -> None:
    """Self-maintaining: fires for exactly the operations that actually declare
    both paging params (currently 15/16 list_* operations -- list_server_types
    is the one exception, with neither param), never hand-listed by name."""
    operations = load_operations(spec_path)
    for op in operations.values():
        param_names = {p.name for p in op.parameters}
        has_paging_params = {"page_size", "page_number"} <= param_names
        mentions_pagination = "page_size" in description_for(op, None)
        assert has_paging_params == mentions_pagination, op.operation_id


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_call_substitutes_path_params_and_sends_pat_auth() -> None:
    operations = load_operations(_SERVER_SPEC)
    op = operations["rebuild_server"]
    client = CmpApiClient(
        base_url="https://cmp.example.com", auth_header={"Authorization": "token secret-pat"}
    )

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(202, json={"status": "accepted"})

    result = client.call(op, {"server_id": "abc-123"}, client=_mock_client(handler))

    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == "https://cmp.example.com/admin-v2/server/servers/abc-123/rebuild/"
    assert request.headers["Authorization"] == "token secret-pat"
    assert result == {"status_code": 202, "data": {"status": "accepted"}}


def test_call_splits_query_params_from_body_and_sends_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations(_SERVER_SPEC)
    op = operations["list_compute_nodes"]

    monkeypatch.setenv("TEST_BASIC_BASE_URL", "https://cmp.example.com")
    monkeypatch.setenv("TEST_BASIC_USERNAME", "admin")
    monkeypatch.setenv("TEST_BASIC_PASSWORD", "hunter2")
    client = CmpApiClient.from_env("TEST_BASIC")

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"results": []})

    client.call(op, {"hostname": "compute-01"}, client=_mock_client(handler))

    request = captured["request"]
    assert request.method == "GET"
    assert request.url.params["hostname"] == "compute-01"
    assert request.headers["Authorization"].startswith("Basic ")


def test_call_without_base_url_reports_not_configured() -> None:
    operations = load_operations(_SERVER_SPEC)
    op = operations["rebuild_server"]
    client = CmpApiClient(base_url="", auth_header={})
    result = client.call(op, {"server_id": "abc-123"})
    assert result == {
        "error": "not_configured",
        "message": "rebuild_server: no base URL configured",
    }


@pytest.mark.parametrize(
    "spec_path,name",
    [
        (_SERVER_SPEC, "cmp-server"),
        (_BLOCK_STORAGE_SPEC, "cmp-block-storage"),
        (_NETWORK_SPEC, "cmp-network"),
    ],
)
def test_build_server_succeeds_for_every_real_spec(spec_path: Path, name: str) -> None:
    server = build_server(name, spec_path, env_prefix="TEST_SMOKE")
    assert server.name == name


def test_load_annotations_reads_the_curated_overlay() -> None:
    annotations = load_annotations(_SERVER_ANNOTATIONS)
    rebuild = annotations["rebuild_server"]
    assert rebuild.risk_level == "medium"
    assert rebuild.tool_category == "action"
    assert "project_id" in rebuild.usage_note


def test_load_annotations_missing_file_returns_empty() -> None:
    assert load_annotations(_SPECS_DIR / "does_not_exist.json") == {}


@pytest.mark.parametrize(
    "name,spec_path,annotations_path",
    [
        ("server", _SERVER_SPEC, _SERVER_ANNOTATIONS),
        ("block_storage", _BLOCK_STORAGE_SPEC, _BLOCK_STORAGE_ANNOTATIONS),
        ("network", _NETWORK_SPEC, _NETWORK_ANNOTATIONS),
    ],
)
def test_every_real_operation_has_a_curated_annotation(
    name: str, spec_path: Path, annotations_path: Path
) -> None:
    """Every operation in the real spec gets real, hand-written guidance --
    not just the method-derived hint -- so an agent choosing a tool has more
    to go on than a bare OpenAPI summary. Also catches a typo'd operationId
    in the annotation file silently doing nothing (an orphaned key)."""
    operation_ids = set(load_operations(spec_path))
    annotated_ids = set(load_annotations(annotations_path))

    missing = operation_ids - annotated_ids
    assert not missing, f"{name}: operations with no curated annotation: {sorted(missing)}"

    orphaned = annotated_ids - operation_ids - _LEGACY_ANNOTATION_KEYS[name]
    assert not orphaned, f"{name}: annotation keys matching no real operation: {sorted(orphaned)}"


@pytest.mark.parametrize(
    "name,annotations_path",
    [
        ("server", _SERVER_ANNOTATIONS),
        ("block_storage", _BLOCK_STORAGE_ANNOTATIONS),
        ("network", _NETWORK_ANNOTATIONS),
    ],
)
def test_every_curated_annotation_has_a_usage_note(name: str, annotations_path: Path) -> None:
    """A `risk_level`/`tool_category` with no explanation is a guess wearing a
    label -- every entry must say why, not just what."""
    annotations = load_annotations(annotations_path)
    empty = [op_id for op_id, entry in annotations.items() if not entry.usage_note.strip()]
    assert not empty, f"{name}: curated entries with no usage_note: {sorted(empty)}"


@pytest.mark.asyncio
async def test_get_operations_are_read_only_and_idempotent_without_curation() -> None:
    """No curated entry exists for this GET -- the method-derived hint must still fire."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_server("cmp-server", _SERVER_SPEC, env_prefix="TEST_ANNOTATIONS")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        tool = next(t for t in tools.tools if t.name == "get_server_compute_node")
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_tool_result_validates_against_its_declared_output_schema() -> None:
    """The real regression this wrapping exists for: if outputSchema were the
    bare resolved schema instead of the {status_code,data}/{error,message}
    envelope, the SDK's own internal jsonschema.validate on every call_tool
    result would fail -- proven here by actually calling a tool with a real
    outputSchema declared and letting the SDK validate it, not by asserting
    the schema shape in isolation."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_server("cmp-server", _SERVER_SPEC, env_prefix="TEST_OUTPUT_SCHEMA")
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        tool = next(t for t in tools.tools if t.name == "get_compute_node")
        assert tool.outputSchema is not None

        result = await session.call_tool("get_compute_node", {"compute_id": "test"})

    assert result.isError is False
    assert result.structuredContent == {
        "error": "not_configured",
        "message": "get_compute_node: no base URL configured",
    }


@pytest.mark.asyncio
async def test_curated_annotations_enrich_the_description_and_hints() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_server(
        "cmp-server",
        _SERVER_SPEC,
        env_prefix="TEST_ANNOTATIONS",
        annotations_path=_SERVER_ANNOTATIONS,
    )
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        rebuild = next(t for t in tools.tools if t.name == "rebuild_server")
        delete = next(t for t in tools.tools if t.name == "delete_server")

        assert "Risk level:" not in rebuild.description
        assert "Category: action" in rebuild.description
        # POST with no curated override on the bool hints -> method default stands.
        assert rebuild.annotations.idempotentHint is False

        # DELETE's method-derived destructiveHint stands even though delete_server
        # has a curated entry that doesn't itself override the boolean hints.
        assert delete.annotations.destructiveHint is True
        assert "FORCE_DELETE_CORE_RETRIES" in delete.description
