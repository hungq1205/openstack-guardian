# Mock CMP admin API

A minimal local stand-in for the real CMP admin-v2 (and admin-v1) API, for
exercising `cmp_admin_mcp` with real HTTP round trips instead of the
`{"error": "not_configured", ...}` short-circuit that fires when no base URL
is set. Also serves a small interactive web dashboard (`dashboard.html`, at
`http://127.0.0.1:8081/`) for driving 3 scripted incidents by hand -- see
"Interactive dashboard" below.

**2026-09-27: `cmp-admin`'s real tool catalog is trimmed to exactly 7 operations** -- the ones the
failure-pattern KB actually names or implies, with the `server` domain temporarily pinned to
`server_v1` only (CMP's own v2 migration isn't far enough along yet; switch back once it is):
`admin_api_servers_retrieve`/`list`/`action_log`/`recreate`, `get_elastic_ip`/`get_private_ip`,
and `get_volume_legacy` (opt-in, `CMP_ADMIN_V2_ENABLE_LEGACY_VOLUME_GET=1`). Everything else this
mock can still echo is `enabled=False` in the tool registry and unreachable from a real
investigation -- edit the Catalog page or `guardian_platform.config_store` directly to change
that.

Three layers:

1. **A stateful fake incident**, for the one server id it's seeded with
   (`4a76a7df-f2dd-479f-bcf7-118a19f71c40`), *found but suspended* until
   recreated -- matching a real Nova/CMP environment where the instance
   record exists but a connectivity failure left it stuck rather than
   actually gone:
   - `admin_api_servers_retrieve` returns **200**, `status: "suspended"` /
     `power_state: "suspended"`
   - `admin_api_servers_list` / `list_server_status` **include it** in
     `results`, same suspended values
   - `admin_api_servers_action_log` returns one entry (the failed create,
     with a `fault` matching the `connection_aborted_transient` KB pattern)

   Calling `admin_api_servers_recreate` on that id flips it to
   `status: "active"` / `power_state: "running"`, plus a second
   action-log entry -- so `assemble_log` can be driven through a real
   find-diagnose-fix-verify cycle against this one id, not just a
   request-shape echo.
2. **The 3 scripted "server creation failed" incidents** (`scenarios.py`) --
   `block_device_not_bootable`, `volume_status_drift`, `port_status_drift` --
   one per failure-pattern KB entry whose documented fix is the same CMP API
   call, `admin_api_servers_recreate`. Each names a server that never
   actually got created (`admin_api_servers_retrieve` 404s until resolved --
   these are all "Error generating server ...", not a status CMP's own
   status enum has a failed/error member for); the volume/port ones also
   register a dependent resource (`GET /admin-api/volumes/{id}/`,
   `GET /admin-v2/network/elastic-ips/{id}/` or `.../private-ips/{id}/`)
   whose status always comes back healthy, matching the KB's "checked, and
   it wasn't actually in-use" branch. Driven by `simulate_scenario`/
   `resolve_scenario` in `server.py`, both usable from Python, from
   `POST /_mock/scenarios/{id}/simulate` \| `/resolve`, or from the
   dashboard's buttons -- see below.
3. **A generic echo fallback** for every other path/id/method: `200
   {METHOD} {PATH}` as a plain-text body -- e.g. requesting `rebuild_server`
   for `server_id=abc-123` gets back
   `"POST /admin-v2/server/servers/abc-123/rebuild/"`. Enough to confirm any
   *other* tool call built its request correctly (path params substituted,
   query string attached) without needing to fake realistic response shapes
   for ~100 different operations.

State lives only in this process's memory -- restarting the server resets
every fake record (including the 3 scenarios) back to seeded/broken.

## Setup

```bash
python mock/cmp_server/server.py
# listens on http://127.0.0.1:8081 by default; --host/--port to change
# --es-url/--es-index point at the mock Elasticsearch for log injection
# (below); --no-es disables it entirely
```

## Interactive dashboard

Open **http://127.0.0.1:8081/** for 3 cards, one per scripted incident, each
with:

- its KB id, cause, and the KB's own remediation text (collapsed by default)
- the exact log line the last "Simulate failure" click produced (and whether
  it made it into Elasticsearch)
- **Simulate failure** -- breaks the server (`available=False`, so retrieve
  404s again) and re-seeds its dependent resource as healthy, then
  best-effort logs the matching `ERROR celery.server_creator ...` line to
  the mock Elasticsearch's `iaas-api-*` index (see
  `mock/elasticsearch/README.md`) with real `level`/`message`/`hostname`
  fields -- if that Elasticsearch is running and seeded, the same incident
  shows up on Guardian's own Errors page, ready to attach to a real
  investigation.
- **Resolve now (recreate)** -- calls the exact same code the real
  `POST /admin-api/servers/{id}/recreate/` route runs, so clicking it here
  and driving the real investigate-incident flow against this mock (`export
  CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081` for `cmp-admin`) reach the
  same fix, one code path either way.

The dashboard polls `GET /_mock/scenarios` every 5s, so it also reflects a
fix made through the real MCP tool, not just its own buttons.

Then point `cmp-admin` at it:

```bash
export CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081
uv run python -m mcp_servers.cmp_admin_mcp.main
```

No credentials needed -- the mock never checks the `Authorization` header,
though `CmpApiClient` still sends whatever `CMP_ADMIN_V2_PAT`/
`CMP_ADMIN_V2_USERNAME`+`CMP_ADMIN_V2_PASSWORD` you've set (or none at all).

## Driving the seeded incident end to end

```bash
SID=4a76a7df-f2dd-479f-bcf7-118a19f71c40

curl -i http://127.0.0.1:8081/admin-api/servers/$SID/
# -> 200 {"status": "suspended", "power_state": "suspended", ...} -- found, but stuck

curl http://127.0.0.1:8081/admin-api/servers/
# -> {"count": 1, "results": [{"id": "...", "status": "suspended", ...}]}

curl http://127.0.0.1:8081/admin-api/servers/$SID/action-log/
# -> {"count": 1, "results": [{"action": "create", ..., "message": "Error generating server ..."}]}

curl -X POST http://127.0.0.1:8081/admin-api/servers/$SID/recreate/
# -> {"detail": "recreate accepted", "id": "..."}

curl http://127.0.0.1:8081/admin-api/servers/$SID/
# -> 200 {"status": "active", "power_state": "running", ...} -- back up

curl http://127.0.0.1:8081/admin-api/servers/
# -> {"count": 1, "results": [{"id": "...", "status": "active", ...}]}
```

`admin_api_servers_action_log` for the same id shows both events (the
original create failure, then the recreate) once you've called recreate.

To register more fake servers or a different scripted resolution, import
`add_fake_server`/`add_fake_action_log_entry`/`resolve_fake_server` from
`server.py` and call them before `run()` -- see the module docstring. To add
a 4th scripted incident, add a `Scenario` to `scenarios.py`'s `SCENARIOS`
dict -- `server.py` and the dashboard both pick it up automatically.

## What a generic (non-seeded) call looks like

```
> call_tool("rebuild_server", {"server_id": "abc-123", "project_id": "proj-1"})
{"status_code": 200, "data": {"raw": "POST /admin-v2/server/servers/abc-123/rebuild/"}}
```

The body isn't JSON, so `CmpApiClient.call` falls back to its
`{"raw": response.text}` wrapping (the same path a real non-JSON upstream
response would take) -- this is expected, not a bug in the mock.

## What this is not

Not a fixture for the automated test suite (`tests/` uses
`httpx.MockTransport`, not a real socket) and not a general functional
simulation of the CMP API -- it can only fake the 4 scripted incidents (the
original suspended-server one plus the 3 in `scenarios.py`) in detail; every
other id/operation still just echoes its own request line. For anything
beyond those, read the echoed request line, or run with `--host 0.0.0.0` and
watch stdout (one line per request) while driving cmp-admin from a real MCP
client. Both dependent-resource GETs (volume/port) always answer healthy --
the KB's other branch, genuinely still in-use, isn't modeled.
