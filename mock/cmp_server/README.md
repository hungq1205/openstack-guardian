# Mock CMP admin API

A minimal local stand-in for the real CMP admin-v2 (and admin-v1) API, for
exercising `cmp_admin_mcp` with real HTTP round trips instead of the
`{"error": "not_configured", ...}` short-circuit that fires when no base URL
is set.

Two layers:

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
2. **A generic echo fallback** for every other path/id/method: `200
   {METHOD} {PATH}` as a plain-text body -- e.g. requesting `rebuild_server`
   for `server_id=abc-123` gets back
   `"POST /admin-v2/server/servers/abc-123/rebuild/"`. Enough to confirm any
   *other* tool call built its request correctly (path params substituted,
   query string attached) without needing to fake realistic response shapes
   for ~100 different operations.

State lives only in this process's memory -- restarting the server resets
the fake incident back to suspended.

## Setup

```bash
python mock/cmp_server/server.py
# listens on http://127.0.0.1:8081 by default; --host/--port to change
```

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
`server.py` and call them before `run()` -- see the module docstring.

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
simulation of the CMP API -- it can only fake the one seeded incident in
detail; every other id/operation still just echoes its own request line.
For anything beyond the seeded incident, read the echoed request line, or
run with `--host 0.0.0.0` and watch stdout (one line per request) while
driving cmp-admin from a real MCP client.
