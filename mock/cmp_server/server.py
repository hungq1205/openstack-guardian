"""Mock CMP admin API for `cmp-admin` (cmp_admin_mcp) to connect to.

Two layers:

1. **Stateful fake servers** (`_FAKE_SERVERS`, `_FAKE_ACTION_LOGS`,
   `_FAKE_AVAILABILITY`): a small, in-memory, per-id server registry for a
   handful of admin-api v1 routes (retrieve/list/list-server-status/
   action-log/recreate) -- enough to simulate one real incident end to end.
   The seeded incident server is *found* the whole time -- `admin_api_
   servers_retrieve` returns 200 and it shows up in `admin_api_servers_list`/
   `list_server_status` -- but its `power_state`/`status` read `suspended`,
   matching a real Nova/CMP environment where the instance record exists but
   the connection to core dropped mid-operation, leaving it stuck rather
   than actually gone. The designated fix (`admin_api_servers_recreate`)
   flips it to `status: "active"` / `power_state: "running"` when called.
   `admin_api_servers_action_log` always returns its history regardless of
   this state (the creation attempt happened and left a real log entry
   either way). Seeded with one entry matching the `connection_aborted_transient` \
KB pattern (server id `4a76a7df-f2dd-479f-bcf7-118a19f71c40`, suspended \
until recreated) -- see `add_fake_server`/`resolve_fake_server` to add or \
wire up more. `available=False` is still supported on `add_fake_server` for a
   *different* kind of incident (a build that never got an instance record at
   all, so retrieve genuinely 404s) -- just not the one seeded by default.

2. **Generic echo fallback**: any request that doesn't match a known id/route
   above gets a 200 whose body is just `"{METHOD} {PATH}"` -- same as
   before, still enough to turn any *other* cmp-admin tool call from a
   `{"error": "not_configured", ...}` short-circuit into a real HTTP round
   trip you can see, without needing to fake every one of ~100 operations'
   response shapes.

Stdlib-only (`http.server`), matching mock/elasticsearch's no-new-dependency
convention -- this is a throwaway local script, not part of the `cmp-mcp`
package itself. State lives only in this process's memory: restarting the
server resets every fake record back to its seeded state.

Usage:
    python mock/cmp_server/server.py [--host HOST] [--port PORT]

Then point cmp-admin at it:
    CMP_ADMIN_V2_BASE_URL=http://127.0.0.1:8081 uv run python -m mcp_servers.cmp_admin_mcp.main

Try the seeded incident end to end:
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
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8081

_CONNECTION_ABORTED_MESSAGE = (
    "Unable to establish connection to https://10.211.254.1:8774/v2.1/servers: "
    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
)

_lock = threading.Lock()

_FAKE_SERVERS: dict[str, dict[str, Any]] = {}
_FAKE_ACTION_LOGS: dict[str, list[dict[str, Any]]] = {}
_FAKE_AVAILABILITY: dict[str, bool] = {}


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
    """
    record: dict[str, Any] = {
        "id": server_id,
        "hostname": hostname,
        "status": status,
        "power_state": power_state,
        "region": region,
        "project": project,
        "task_state": None,
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
        record["task_state"] = None
        record.pop("fault", None)
        _FAKE_AVAILABILITY[server_id] = True
    return True


# Seed the one incident this mock was built to demonstrate: a server that
# exists and is found (`available=True`), but stuck `suspended` from a Nova
# connectivity failure during creation, matching the
# `connection_aborted_transient` KB entry's signature. `status`/`power_state`
# must be real values from the admin-v1 spec's own enums (all lowercase,
# server_v1.json's ServerAdminDetailStatusEnum/...PowerStateEnum) since a
# validated tool call checks the response against them -- "error"/"nostate"
# aren't members of either enum, but "suspended" is a valid member of both.
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
    message=(
        "Error generating server 4a76a7df-f2dd-479f-bcf7-118a19f71c40: " + _CONNECTION_ABORTED_MESSAGE
    ),
)

_RETRIEVE_RE = re.compile(r"^/admin-api/servers/([^/]+)/$")
_ACTION_LOG_RE = re.compile(r"^/admin-api/servers/([^/]+)/action-log/$")
_RECREATE_RE = re.compile(r"^/admin-api/servers/([^/]+)/recreate/$")
_LIST_PATH = "/admin-api/servers/"
_LIST_STATUS_PATH = "/admin-api/servers/list-server-status/"


def _server_list_payload(server_id_filter: str | None) -> dict[str, Any]:
    with _lock:
        records = deepcopy(
            [record for sid, record in _FAKE_SERVERS.items() if _FAKE_AVAILABILITY.get(sid)]
        )
    if server_id_filter is not None:
        records = [record for record in records if record["id"] == server_id_filter]
    return {"count": len(records), "results": records}


class _MockCmpHandler(BaseHTTPRequestHandler):
    """Serves stateful fake data for the seeded server id(s) on a handful of
    admin-api v1 routes, and falls back to echoing `"{method} {path}"` for
    everything else -- see module docstring.
    """

    protocol_version = "HTTP/1.1"

    def _write_json(self, status_code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
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

        if path == _LIST_PATH or path == _LIST_STATUS_PATH:
            id_filter = query.get("id", [None])[0]
            self._write_json(200, _server_list_payload(id_filter))
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
                self._write_json(200, {"count": len(entries), "results": entries})
                return

        self._write_echo()

    def do_POST(self) -> None:  # noqa: N802
        self._read_body()  # drain the request body before responding either way
        match = _RECREATE_RE.match(urlsplit(self.path).path)
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
    print(f"  Fake servers seeded: {list(_FAKE_SERVERS)}")
    print("  Everything else echoes its own method+path.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
