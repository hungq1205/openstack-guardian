"""Mock CMP admin API for `cmp-admin` (cmp_admin_mcp) to connect to -- and, since 2026-09-27, a
small interactive web UI (`dashboard.html`) for driving 3 scripted incidents by hand.

**2026-09-27: `cmp-admin`'s own real tool catalog is now deliberately trimmed to exactly the 7
operations the failure-pattern KB actually names or implies** (the server domain temporarily
pinned to `server_v1` only -- CMP is mid-migration to v2 and not yet well-defined there, so v1's
`admin_api_servers_recreate` is what every KB entry's fix routes through for now, including the
3 that literally say "rebuild_server", v2's name for the same idea -- switch the `server`/
`server_v1` spec-source rows back to prefer v2 once that migration is further along):
`admin_api_servers_retrieve`/`list`/`action_log`/`recreate` (v1), `get_elastic_ip`/`get_private_ip`
(v2 network, unaffected by the v1/v2 server decision), `get_volume_legacy` (opt-in stopgap, the
only way to satisfy `volume_status_drift`'s volume-status check at all -- see
`legacy_operations.py`). Every other real cmp-admin operation still exists in the spec and this
mock could still echo it, but is `enabled=False` in the tool registry now, so no real
investigation ever reaches it -- this mock's 3 scripted incidents plus the seeded one exercise
100% of what's actually left reachable.

Three layers:

1. **Stateful fake servers** (`_FAKE_SERVERS`, `_FAKE_ACTION_LOGS`, `_FAKE_AVAILABILITY`): a small,
   in-memory, per-id server registry for a handful of admin-api v1 routes (retrieve/list/
   list-server-status/action-log/recreate) -- enough to simulate one real incident end to end.
   The seeded incident server is *found* the whole time -- `admin_api_servers_retrieve` returns
   200 and it shows up in `admin_api_servers_list`/`list_server_status` -- but its
   `power_state`/`status` read `suspended`, matching a real Nova/CMP environment where the
   instance record exists but the connection to core dropped mid-operation, leaving it stuck
   rather than actually gone. The designated fix (`admin_api_servers_recreate`) flips it to
   `status: "active"` / `power_state: "running"` when called. `admin_api_servers_action_log`
   always returns its history regardless of this state (the creation attempt happened and left a
   real log entry either way). Seeded with one entry matching the `connection_aborted_transient`
   KB pattern (server id `4a76a7df-f2dd-479f-bcf7-118a19f71c40`, suspended until recreated) -- see
   `add_fake_server`/`resolve_fake_server` to add or wire up more. `available=False` is still
   supported on `add_fake_server` for a *different* kind of incident (a build that never got an
   instance record at all, so retrieve genuinely 404s) -- the one every scenario below actually
   uses.

2. **The 3 scripted incidents** (`scenarios.py`): `block_device_not_bootable`,
   `volume_status_drift`, `port_status_drift` -- one per KB entry whose fix is
   `admin_api_servers_recreate`. Each is a server that never got created (`available=False`, so
   retrieve 404s -- these 3 are all "Error generating server ...", never a status Nova/CMP's own
   enum actually has a member for); the volume/port ones also register a dependent resource whose
   GET always comes back healthy, demonstrating the KB's "check it, then recreate" happy path.
   `simulate_scenario`/`resolve_scenario` (below) drive these, and are what both the dashboard's
   buttons and a direct `POST .../recreate/` (the real CMP API call an agent would make) go
   through -- one code path either way. `simulate_scenario` also best-effort POSTs the matching
   ERROR log line into the mock Elasticsearch (`mock/elasticsearch/`) cmp-logs index, so the same
   incident shows up in Guardian's own Errors page, not just this mock's own state.

3. **Generic echo fallback**: any request that doesn't match a known id/route above gets a 200
   whose body is just `"{METHOD} {PATH}"`. Now mostly moot for a real investigation -- almost
   every operation this would have stood in for is `enabled=False` in cmp-admin's tool registry
   (see above) and never callable at all -- but still there for manual `curl` exploration of the
   ~78 disabled operations' request shapes.

Stdlib-only (`http.server`), matching mock/elasticsearch's no-new-dependency convention -- this is
a throwaway local script, not part of the `cmp-mcp` package itself. State lives only in this
process's memory: restarting the server resets every fake record back to its seeded state.

Usage:
    python mock/cmp_server/server.py [--host HOST] [--port PORT]
                                      [--es-url URL] [--es-index NAME] [--no-es]

Then point cmp-admin at it:
    CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081 uv run python -m mcp_servers.cmp_admin_mcp.main

Open the dashboard: http://127.0.0.1:8081/ -- click "Simulate failure" on any of the 3 cards, then
"Resolve now" (or run the real investigate-incident flow against it) to watch it flip back.

Try the original seeded incident end to end:
    curl http://127.0.0.1:8081/admin-api/servers/4a76a7df-f2dd-479f-bcf7-118a19f71c40/
        -> 200, status: "", power_state: "suspended" -- found, but stuck
    curl http://127.0.0.1:8081/admin-api/servers/4a76a7df-f2dd-479f-bcf7-118a19f71c40/action-log/
        -> 200, one "create" entry with the connection-aborted fault text
    curl -X POST http://127.0.0.1:8081/admin-api/servers/4a76a7df-f2dd-479f-bcf7-118a19f71c40/recreate/
        -> flips it to active/running, and appends a "recreate" action-log entry
    curl http://127.0.0.1:8081/admin-api/servers/4a76a7df-f2dd-479f-bcf7-118a19f71c40/
        -> 200, status: "active", power_state: "running"
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import urllib.error
import urllib.request
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from scenarios import SCENARIOS, Scenario, render_log_line

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8081
_DEFAULT_ES_URL = "http://127.0.0.1:9200"
_DEFAULT_ES_INDEX = "iaas-api-2026.08.23"

_DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")

_CONNECTION_ABORTED_MESSAGE = (
    "Unable to establish connection to https://10.211.254.1:8774/v2.1/servers: "
    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
)

_lock = threading.Lock()

_FAKE_SERVERS: dict[str, dict[str, Any]] = {}
_FAKE_ACTION_LOGS: dict[str, list[dict[str, Any]]] = {}
_FAKE_AVAILABILITY: dict[str, bool] = {}

# Dependent resources (volumes/ports) the volume/port-drift scenarios check before recreating --
# keyed by id, always "healthy" (see module docstring point 2).
_FAKE_VOLUMES: dict[str, dict[str, Any]] = {}
_FAKE_PORTS: dict[str, dict[str, Any]] = {}

# Set by `configure_es` / `main`'s arg parsing; `None` disables log injection entirely (--no-es).
_ES_URL: str | None = _DEFAULT_ES_URL
_ES_INDEX: str = _DEFAULT_ES_INDEX

# Per-scenario bookkeeping the dashboard shows: whether it's ever been simulated, and the exact
# log line + document id from the *last* simulate (so "Resolve now" and the card's log preview
# describe the same incident, and repeat simulates get distinct document ids).
_SCENARIO_STATE: dict[str, dict[str, Any]] = {sid: {"last_log_line": None, "last_doc_id": None} for sid in SCENARIOS}


def add_fake_server(
    server_id: str,
    *,
    hostname: str,
    status: str,
    power_state: str = "suspended",
    fault_message: str | None = None,
    region: str = "hn",
    project: str = "demo-project",
    available: bool = False,
) -> None:
    """Register (or overwrite) one fake server record, thread-safely.

    `available=False` (the default) simulates a server that never actually
    finished creating: `admin_api_servers_retrieve` returns 404, and it's
    left out of `admin_api_servers_list`/`list_server_status` results --
    matching a real Nova/CMP environment, where a build that died before
    the instance record was ever committed has nothing to look up by id.
    `resolve_fake_server` (called by the fake `recreate` handler) is what
    flips this to `True`.

    The record shape matches admin-v1's real `ServerAdminDetail` response
    schema in full (`server_v1.json`) -- every one of its 16 required
    fields, including nested `flavor`/`key_pair` objects and empty
    `elastic_ip`/`private_ip`/`volumes`/`security_groups` arrays -- since
    `cmp_admin_mcp`'s `admin_api_servers_retrieve` validates the response
    against that schema and previously 500'd with an output-validation
    error on the old, minimal record (missing `flavor`, `key_pair`, etc.
    entirely; found live 2026-09-27 driving a real investigation, ticket
    #94). `status`/`power_state` must be real members of
    `ServerAdminDetailStatusEnum`/`...PowerStateEnum` for the same reason --
    `""` is not a member of either, so an unresolved scripted incident
    (which always 404s via `available=False` before its status is ever
    actually read) still needs a schema-valid placeholder, not blank.
    """
    now = datetime.now(UTC).isoformat()
    record: dict[str, Any] = {
        "id": server_id,
        "name": hostname,
        "hostname": hostname,
        "image_name": f"{hostname}-image",
        "status": status or "building",
        "power_state": power_state or "pending",
        "region": region,
        "project": project,
        "project_slug": project,
        "zone": str(uuid.uuid5(uuid.NAMESPACE_URL, f"zone:{region}")),
        # `dpc`/`owner`/`bandwidth` are all `nullable: true` in the real schema, and all
        # `None` for plenty of real servers -- but `cmp_admin_mcp`'s OpenAPI bridge doesn't
        # currently translate `nullable: true` into a real `type: [X, "null"]`, so a live
        # response with any of them actually null would *also* fail this same output-validation
        # check (found live 2026-09-27, ticket #94 -- a gap in cmp-mcp itself, not this mock).
        # Giving every fake server real, non-null values sidesteps it here rather than
        # reproducing a case cmp-admin can't validate; `task_state` is genuinely optional
        # (absent from `required`) so it's left out entirely instead.
        "dpc": str(uuid.uuid5(uuid.NAMESPACE_URL, "dpc:demo")),
        "owner": project,
        "bandwidth": 100,
        # Not actually `None` despite the field being nullable in the real API -- see
        # `add_fake_server`'s docstring: `cmp_admin_mcp`'s own OpenAPI bridge doesn't currently
        # translate `nullable: true` into a real `type: [object, "null"]` for an `allOf`-wrapped
        # ref like this one, so a live server response with a genuinely absent placement group
        # would *also* fail this same output-validation check -- a gap in cmp-mcp itself, not
        # just this mock (found live 2026-09-27, ticket #94). Giving every fake server a real
        # placement group sidesteps it here rather than working around it in cmp-mcp.
        "placement_group": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "placement-group:demo")),
            "display_name": "Demo Placement Group",
            "name": "demo-placement-group",
            "server_count": 1,
            "region": region,
            "created_at": now,
            "updated_at": now,
            "owner": project,
            "project": project,
        },
        "security_groups": [],
        "elastic_ip": [],
        "private_ip": [],
        "volumes": [],
        "created_at": now,
        "updated_at": now,
        "flavor": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "flavor:demo")),
            "name": "demo-flavor",
            "display_name": "Demo Flavor",
            "region": region,
            "zone": str(uuid.uuid5(uuid.NAMESPACE_URL, f"zone:{region}")),
            "ram": 4096,
            "vcpus": 2,
            "gpus": 0,
            "family": "general-purpose",
            "status": "enabled",
            "created_at": now,
            "updated_at": now,
        },
        "key_pair": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "keypair:demo")),
            "name": "demo-keypair",
            "display_name": "Demo Keypair",
            "type": "rsa",
            "project": project,
            "owner": project,
            "created_at": now,
            "updated_at": now,
        },
    }
    if fault_message is not None:
        record["fault"] = {"message": fault_message, "code": 500}
    with _lock:
        _FAKE_SERVERS[server_id] = record
        _FAKE_AVAILABILITY[server_id] = available


def add_fake_action_log_entry(
    server_id: str, *, action: str, message: str, request_id: str | None = None
) -> None:
    entry = {
        "action": action,
        "request_id": request_id,
        "start_time": datetime.now(UTC).isoformat(),
        "message": message,
    }
    with _lock:
        _FAKE_ACTION_LOGS.setdefault(server_id, []).append(entry)


def resolve_fake_server(server_id: str, *, status: str = "active", power_state: str = "running") -> bool:
    """Flip a fake server's status (e.g. after a simulated fix), clear its
    fault, and mark it available -- retrieve/list start returning it as a
    real record instead of 404/omitted. Returns False if `server_id` isn't
    a known fake server."""
    with _lock:
        record = _FAKE_SERVERS.get(server_id)
        if record is None:
            return False
        record["status"] = status
        record["power_state"] = power_state
        record.pop("task_state", None)
        record["updated_at"] = datetime.now(UTC).isoformat()
        record.pop("fault", None)
        _FAKE_AVAILABILITY[server_id] = True
    return True


# Seed the one incident this mock was originally built to demonstrate: a server that exists and is
# found (`available=True`), but stuck `suspended` from a Nova connectivity failure during
# creation, matching the `connection_aborted_transient` KB entry's signature. `status`/
# `power_state` must be real values from the admin-v1 spec's own enums (all lowercase,
# server_v1.json's ServerAdminDetailStatusEnum/...PowerStateEnum) since a validated tool call
# checks the response against them -- "error"/"nostate" aren't members of either enum, but
# "suspended" is a valid member of both.
add_fake_server(
    "4a76a7df-f2dd-479f-bcf7-118a19f71c40",
    hostname="demo-server-01",
    status="suspended",
    power_state="suspended",
    fault_message=_CONNECTION_ABORTED_MESSAGE,
    available=True,
)
add_fake_action_log_entry(
    "4a76a7df-f2dd-479f-bcf7-118a19f71c40",
    action="create",
    request_id="04ec7308-8cfc-4034-a117-2b204751864b",
    message=("Error generating server 4a76a7df-f2dd-479f-bcf7-118a19f71c40: " + _CONNECTION_ABORTED_MESSAGE),
)


# --------------------------------------------------------------------------------------------
# The 3 scripted incidents (scenarios.py)
# --------------------------------------------------------------------------------------------


def _as_private_ip_record(port: dict[str, Any]) -> dict[str, Any]:
    """The KB's port-drift instruction says to check `GET .../elastic-ips/{id}/` *or*
    `.../private-ips/{id}/` -- an agent may call either. `get_private_ip`'s real response schema
    (`PrivateIPAdminDetailSchema`) is genuinely different from `get_elastic_ip`'s
    (`ElasticIPAdminDetailSchema`) -- `device_owner`/`subnet` required instead of
    `enable_ipv4`/`enable_ipv6`, no top-level `status` at all -- so the same `_FAKE_PORTS` record
    can't be served as-is for this route; it needs reshaping. Found live 2026-09-27 (ticket #95),
    right after the elastic-ip route's own shape was fixed for ticket #94 -- the two routes were
    never actually interchangeable despite sharing one fake registry."""
    return {
        "id": port["id"],
        "ip_address": port.get("ip_address"),
        "device_owner": "user",
        "subnet": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "subnet:demo")),
            "name": "demo-subnet",
            "cidr": "10.20.30.0/24",
        },
        "region": port["region"],
        "project": port["project"],
        "created_at": port["created_at"],
        "updated_at": port["updated_at"],
    }


def _seed_dependent_healthy(scenario: Scenario) -> None:
    """(Re)registers the scenario's dependent volume/port as healthy -- the KB's "checked and it
    wasn't actually in-use" branch. Called once at import time and again on every simulate, so a
    scenario that was never touched still has a real GET response, not a 404."""
    if scenario.dependent is None:
        return
    kind, resource_id = scenario.dependent
    now = datetime.now(UTC).isoformat()
    with _lock:
        if kind == "volume":
            _FAKE_VOLUMES[resource_id] = {
                "id": resource_id,
                "status": "available",
                "bootable": True,
                "size": 20,
                "created_at": now,
                "updated_at": now,
            }
        else:
            # Shape matches admin-v2's real `ElasticIPAdminDetailSchema` in full (`network.json`)
            # -- `get_elastic_ip` validates the response against it, and the old, minimal record
            # (missing `enable_ipv4`/`enable_ipv6`/`region`/`project` entirely) 500'd with an
            # output-validation error driving a real investigation (found live 2026-09-27, ticket
            # #94). `region`/`project` are nested objects (`NestedRegionSchema`/
            # `NestedProjectSchema`), not the plain strings the v1 server record uses.
            _FAKE_PORTS[resource_id] = {
                "id": resource_id,
                "status": "active",
                "ip_address": "10.20.30.40",
                "enable_ipv4": True,
                "enable_ipv6": False,
                "region": {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "region:hn")),
                    "name": "hn",
                    "description": "Hanoi",
                },
                "project": {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "project:demo-project")),
                    "name": "demo-project",
                    "slug": "demo-project",
                },
                "created_at": now,
                "updated_at": now,
            }


for _scenario in SCENARIOS.values():
    add_fake_server(
        _scenario.server_id,
        hostname=f"{_scenario.id}-demo",
        status="",
        power_state="",
        available=False,  # never actually created -- retrieve 404s until resolved
    )
    _seed_dependent_healthy(_scenario)


def configure_es(url: str | None, index: str) -> None:
    global _ES_URL, _ES_INDEX
    _ES_URL, _ES_INDEX = url, index


def _index_error_log(scenario: Scenario, log_line: str, observed_at_iso: str) -> str | None:
    """Best-effort POST of one ERROR document into the mock cmp-logs index, in the shape
    `error_ingest.py`'s cmp-logs source expects (`level`, `message`, `hostname`, `logger`) plus the
    scenario's own ids as extra (dynamically-mapped) fields, for anyone poking at Elasticsearch/
    Kibana directly. Returns the document id, or `None` if Elasticsearch isn't reachable (printed,
    never raised -- a demo click shouldn't fail just because nobody started `docker compose`)."""
    if _ES_URL is None:
        return None
    doc_id = f"mock-{scenario.id}-{uuid.uuid4().hex[:12]}"
    doc = {
        "@timestamp": observed_at_iso,
        "level": "ERROR",
        "logger": "celery.server_creator",
        "message": log_line,
        "raw_log": log_line,
        "request_id": scenario.template_fields.get("request_id"),
        "hostname": "celery-worker-1",
        "server_id": scenario.server_id,
        "execution_id": scenario.execution_id,
        "scenario_id": scenario.id,
        "source_index": "mock-cmp-server",
    }
    if scenario.dependent is not None:
        kind, resource_id = scenario.dependent
        doc[f"{kind}_id"] = resource_id
    body = json.dumps(doc).encode("utf-8")
    request = urllib.request.Request(
        f"{_ES_URL.rstrip('/')}/{_ES_INDEX}/_doc/{doc_id}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"[mock-cmp-server] could not index log into Elasticsearch ({_ES_URL}): {exc}")
        return None
    return doc_id


def simulate_scenario(scenario_id: str) -> dict[str, Any] | None:
    """Breaks the scenario (server unavailable again, dependent resource re-seeded healthy),
    appends a "create" action-log entry, and best-effort logs the matching ERROR line to
    Elasticsearch. Idempotent to call repeatedly -- each call is its own "attempt", same as a real
    retried build would be. Returns the state dict (see `scenario_state`), or `None` for an
    unknown id."""
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        return None
    with _lock:
        _FAKE_AVAILABILITY[scenario.server_id] = False
    _seed_dependent_healthy(scenario)
    now = datetime.now(UTC)
    observed_at = now.strftime("%b %d, %Y @ %H:%M:%S.%f")[:-3]
    log_line = render_log_line(scenario, observed_at)
    # The action-log's own convention (see the seeded connection_aborted incident above) is the
    # detail message alone, without the syslog-style "ERROR celery.server_creator ..." prefix.
    detail_start = log_line.find("Error generating server")
    detail_message = log_line[detail_start:] if detail_start != -1 else log_line
    add_fake_action_log_entry(
        scenario.server_id,
        action="create",
        request_id=scenario.template_fields.get("request_id"),
        message=detail_message,
    )
    doc_id = _index_error_log(scenario, log_line, now.isoformat())
    with _lock:
        _SCENARIO_STATE[scenario.id] = {"last_log_line": log_line, "last_doc_id": doc_id}
    return scenario_state(scenario_id)


def resolve_scenario(scenario_id: str) -> dict[str, Any] | None:
    """The same fix a real CMP `recreate` call performs -- used by both the dashboard's "Resolve
    now" button and the real `POST /admin-api/servers/{id}/recreate/` route, so there is exactly
    one code path for "the fix", not two that could drift apart."""
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        return None
    resolve_fake_server(scenario.server_id)
    add_fake_action_log_entry(
        scenario.server_id,
        action="recreate",
        message=f"Server {scenario.server_id} recreated successfully; status is now ACTIVE.",
    )
    return scenario_state(scenario_id)


def scenario_state(scenario_id: str) -> dict[str, Any] | None:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        return None
    with _lock:
        resolved = _FAKE_AVAILABILITY.get(scenario.server_id, False)
        extra = dict(_SCENARIO_STATE.get(scenario.id, {}))
        dependent_record = None
        if scenario.dependent is not None:
            kind, resource_id = scenario.dependent
            registry = _FAKE_VOLUMES if kind == "volume" else _FAKE_PORTS
            dependent_record = deepcopy(registry.get(resource_id))
    return {
        "id": scenario.id,
        "kb_id": scenario.kb_id,
        "title": scenario.title,
        "cause": scenario.cause,
        "instruction": scenario.instruction,
        "server_id": scenario.server_id,
        "dependent": {"kind": scenario.dependent[0], "id": scenario.dependent[1]} if scenario.dependent else None,
        "dependent_record": dependent_record,
        "resolved": resolved,
        "last_log_line": extra.get("last_log_line"),
        "last_doc_id": extra.get("last_doc_id"),
    }


def list_scenario_states() -> list[dict[str, Any]]:
    return [scenario_state(sid) for sid in SCENARIOS]


_RETRIEVE_RE = re.compile(r"^/admin-api/servers/([^/]+)/$")
_ACTION_LOG_RE = re.compile(r"^/admin-api/servers/([^/]+)/action-log/$")
_RECREATE_RE = re.compile(r"^/admin-api/servers/([^/]+)/recreate/$")
_LIST_PATH = "/admin-api/servers/"
_LIST_STATUS_PATH = "/admin-api/servers/list-server-status/"

_VOLUME_RE = re.compile(r"^/admin-api/volumes/([^/]+)/$")
_ELASTIC_IP_RE = re.compile(r"^/admin-v2/network/elastic-ips/([^/]+)/$")
_PRIVATE_IP_RE = re.compile(r"^/admin-v2/network/private-ips/([^/]+)/$")

_MOCK_SCENARIOS_PATH = "/_mock/scenarios"
_MOCK_SCENARIO_ACTION_RE = re.compile(r"^/_mock/scenarios/([^/]+)/(simulate|resolve)$")


def _available_records(server_id_filter: str | None) -> list[dict[str, Any]]:
    with _lock:
        records = deepcopy([record for sid, record in _FAKE_SERVERS.items() if _FAKE_AVAILABILITY.get(sid)])
    if server_id_filter is not None:
        records = [record for record in records if record["id"] == server_id_filter]
    return records


def _server_list_payload(server_id_filter: str | None) -> dict[str, Any]:
    """`admin_api_servers_list`'s real response is `PaginatedServerAdminList` -- its `results`
    items are the lighter `ServerAdmin` schema (`display_name`/`has_floating_ip` required,
    `flavor` a plain string, no nested `flavor`/`key_pair`/`placement_group` objects at all),
    genuinely different from `ServerAdminDetail` (what `retrieve` returns) -- not just the same
    record reused. Found live 2026-09-27 driving a real investigation (ticket #94): the old code
    served the *detail* record here, missing `display_name`/`has_floating_ip` entirely."""
    results = [
        {
            "id": r["id"],
            "name": r["name"],
            "hostname": r["hostname"],
            "display_name": r["hostname"],
            "flavor": r["flavor"]["name"],
            "zone": r["zone"],
            "project": r["project"],
            "project_slug": r["project_slug"],
            "power_state": r["power_state"],
            "security_groups": r["security_groups"],
            "elastic_ip": r["elastic_ip"],
            "private_ip": r["private_ip"],
            "has_floating_ip": bool(r["elastic_ip"]),
        }
        for r in _available_records(server_id_filter)
    ]
    # `next`/`previous` are optional (not in `PaginatedServerAdminList.required`) -- omitted
    # rather than sent as `null`, sidestepping the same nullable-not-honored bridge gap
    # `dpc`/`owner`/`bandwidth`/`placement_group` above work around (a real CMP's own
    # single-page response would hit the same issue, just not something this mock can fix).
    return {"count": len(results), "results": results}


def _server_status_list_payload(server_id_filter: str | None) -> dict[str, Any]:
    """`admin_api_servers_list_server_status`'s real response is
    `PaginatedServerStatusAdminList` -- its `results` items are `ServerStatusAdmin`
    (`region`/`updated_at` required, no `id`/`hostname` at all), a third distinct shape from both
    `ServerAdminDetail` and `ServerAdmin` above. Found live 2026-09-27 alongside the `list` bug
    (ticket #94): the old code served the detail record here too, missing `updated_at`."""
    results = [
        {
            "region": r["region"],
            "updated_at": r["updated_at"],
            "power_state": r["power_state"],
            "status": r["status"],
        }
        for r in _available_records(server_id_filter)
    ]
    return {"count": len(results), "next": None, "previous": None, "results": results}


class _MockCmpHandler(BaseHTTPRequestHandler):
    """Serves stateful fake data for the seeded server id(s) on a handful of
    admin-api v1 routes, the 3 scripted scenarios (plus their `/_mock/...` control API and the
    dashboard itself), and falls back to echoing `"{method} {path}"` for everything else -- see
    module docstring.
    """

    protocol_version = "HTTP/1.1"

    def _write_json(self, status_code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_echo(self) -> None:
        body = f"{self.command} {self.path}".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's naming convention
        split = urlsplit(self.path)
        path = split.path
        query = parse_qs(split.query)

        if path in ("/", "/index.html"):
            try:
                html = _DASHBOARD_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                self._write_html(f"<pre>dashboard.html missing: {exc}</pre>")
                return
            self._write_html(html)
            return

        if path == _MOCK_SCENARIOS_PATH:
            self._write_json(200, {"port": self.server.server_address[1], "scenarios": list_scenario_states()})
            return

        if path == _LIST_PATH or path == _LIST_STATUS_PATH:
            id_filter = query.get("id", [None])[0]
            payload = _server_list_payload(id_filter) if path == _LIST_PATH else _server_status_list_payload(id_filter)
            self._write_json(200, payload)
            return

        match = _RETRIEVE_RE.match(path)
        if match:
            server_id = match.group(1)
            with _lock:
                known = server_id in _FAKE_SERVERS
                available = _FAKE_AVAILABILITY.get(server_id, False)
                record = deepcopy(_FAKE_SERVERS.get(server_id))
            if known:
                if available:
                    self._write_json(200, record)
                else:
                    self._write_json(404, {"detail": "Not found."})
                return

        match = _ACTION_LOG_RE.match(path)
        if match:
            server_id = match.group(1)
            with _lock:
                entries = deepcopy(_FAKE_ACTION_LOGS.get(server_id))
            if entries is not None:
                # `admin_api_servers_action_log`'s real response is a plain JSON array
                # (`ActionLogResponse[]`), not a paginated `{count, results}` wrapper -- found
                # live 2026-09-27 (ticket #94) alongside the list/list-server-status bugs above.
                self._write_json(200, entries)
                return

        match = _VOLUME_RE.match(path)
        if match:
            with _lock:
                record = deepcopy(_FAKE_VOLUMES.get(match.group(1)))
            if record is not None:
                self._write_json(200, record)
                return

        match = _ELASTIC_IP_RE.match(path)
        if match:
            with _lock:
                record = deepcopy(_FAKE_PORTS.get(match.group(1)))
            if record is not None:
                self._write_json(200, record)
                return

        match = _PRIVATE_IP_RE.match(path)
        if match:
            with _lock:
                port = deepcopy(_FAKE_PORTS.get(match.group(1)))
            if port is not None:
                self._write_json(200, _as_private_ip_record(port))
                return

        self._write_echo()

    def do_POST(self) -> None:  # noqa: N802
        self._read_body()  # drain the request body before responding either way
        path = urlsplit(self.path).path

        match = _RECREATE_RE.match(path)
        if match:
            server_id = match.group(1)
            if resolve_fake_server(server_id):
                add_fake_action_log_entry(
                    server_id,
                    action="recreate",
                    message=f"Server {server_id} recreated successfully; status is now ACTIVE.",
                )
                self._write_json(202, {"detail": "recreate accepted", "id": server_id})
                return

        match = _MOCK_SCENARIO_ACTION_RE.match(path)
        if match:
            scenario_id, action = match.groups()
            state = simulate_scenario(scenario_id) if action == "simulate" else resolve_scenario(scenario_id)
            if state is not None:
                self._write_json(200, state)
                return
            self._write_json(404, {"detail": f"unknown scenario {scenario_id!r}"})
            return

        self._write_echo()

    def do_PUT(self) -> None:  # noqa: N802
        self._read_body()
        self._write_echo()

    def do_PATCH(self) -> None:  # noqa: N802
        self._read_body()
        self._write_echo()

    def do_DELETE(self) -> None:  # noqa: N802
        self._write_echo()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        print(f"[mock-cmp-server] {self.address_string()} - {format % args}")


def run(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), _MockCmpHandler)
    print(f"Mock CMP server listening on http://{host}:{port}")
    print(f"  Dashboard: http://{host}:{port}/")
    print(f"  Fake servers seeded: {list(_FAKE_SERVERS)}")
    print(f"  Scenarios: {list(SCENARIOS)}")
    print(f"  Elasticsearch log injection: {_ES_URL or 'disabled (--no-es)'} / index {_ES_INDEX!r}")
    print("  Everything else echoes its own method+path.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--es-url", default=_DEFAULT_ES_URL, help=f"default: {_DEFAULT_ES_URL}")
    parser.add_argument("--es-index", default=_DEFAULT_ES_INDEX, help=f"default: {_DEFAULT_ES_INDEX}")
    parser.add_argument("--no-es", action="store_true", help="never try to log simulated failures to Elasticsearch")
    args = parser.parse_args()
    configure_es(None if args.no_es else args.es_url, args.es_index)
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
