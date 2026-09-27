"""The 3 "server creation failed because of a dependent resource" incidents this mock can
simulate -- one per failure-pattern KB entry whose documented fix is the same CMP API call,
`admin_api_servers_recreate` (`/admin-api/servers/{id}/recreate/`):

- `block_device_not_bootable` (KB `block_device_not_bootable_transient`)
- `volume_status_drift`       (KB `volume_status_drift`)
- `port_status_drift`         (KB `port_status_drift`)

Each scenario names a server that never actually got created (Nova/CMP has no record of it --
`admin_api_servers_retrieve` 404s -- matching the KB's own "Error generating server" framing,
not a server stuck in some broken *status*, since CMP admin's own status enum has no failed/error
member to put it in). `volume_status_drift`/`port_status_drift` also name a dependent resource
(a volume, a port) that the KB says to check first: in this mock that check always comes back
healthy (not `in-use`), demonstrating the "safe to recreate" branch -- the KB's other branch
(genuinely still in-use -> notify admin instead) isn't modeled here.

`server.py` is the only thing that mutates registry state; this module is just the data plus the
one line-of-text builder, so `webui.py` (rendering scenario cards) and `server.py` (driving the
fake CMP API + injecting the matching log line) both read from one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DependentKind = Literal["volume", "port"]


@dataclass(frozen=True)
class Scenario:
    id: str
    kb_id: str
    title: str
    cause: str
    """Plain-English, one line: what actually went wrong."""
    instruction: str
    """The KB's own remediation text (Vietnamese, verbatim) -- what an operator/agent is meant to do."""
    server_id: str
    execution_id: str
    signature_template: str
    """The KB's `signature_pattern`, `{placeholder}`s intact."""
    template_fields: dict[str, str]
    """Values for every `{placeholder}` in `signature_template` except `{observed_at}` (filled at
    simulate time, so each click gets its own timestamp)."""
    dependent: tuple[DependentKind, str] | None = field(default=None)
    """(kind, id) of the resource the KB says to check before recreating -- `None` for
    `block_device_not_bootable`, whose KB entry has no such check."""


# Observed-at format matches the pasted example: "Jul 22, 2026 @ 06:50:25.393" (Kibana's own
# Discover CSV export style, %b %d, %Y @ %H:%M:%S.%f trimmed to milliseconds).
_OBSERVED_AT_FORMAT = "%b %d, %Y @ %H:%M:%S.%f"


def render_log_line(scenario: Scenario, observed_at: str) -> str:
    fields = {
        "server_id": scenario.server_id,
        "execution_id": scenario.execution_id,
        **scenario.template_fields,
        "observed_at": observed_at,
    }
    return scenario.signature_template.format(**fields)


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        Scenario(
            id="block_device_not_bootable",
            kb_id="block_device_not_bootable_transient",
            title="Block device not bootable",
            cause="Nova rejected the boot volume as not bootable -- usually a transient/misconfigured state.",
            instruction=(
                'Gọi CMP API recreate server để retry tạo lại (/admin-api/servers/{id}/recreate/).'
            ),
            # Exactly the incident the operator pasted -- same ids, so a manual curl against them
            # matches 1:1 what they already saw in a real log.
            server_id="0a42a11e-a6dc-46ff-9bb6-db6199cb7498",
            execution_id="9004bd2e-e90e-4ff7-8c21-f0681f9ea65e",
            signature_template=(
                "ERROR celery.server_creator server_creator_execution {execution_id} "
                "Error generating server {server_id}: Block Device {device_id} is not bootable. "
                "(HTTP 400) (Request-ID: {request_id}) @timestamp:{observed_at}."
            ),
            template_fields={
                "device_id": "149ad5cd-8377-43f4-abe9-1c46cba8ff1c",
                "request_id": "req-e3ded7c3-653a-4045-bf51-4edfa6f4833c",
            },
        ),
        Scenario(
            id="volume_status_drift",
            kb_id="volume_status_drift",
            title="Volume status drift",
            cause="The boot volume is stuck 'in-use' in core (e.g. from a previous attach) so Nova refuses to reserve it.",
            instruction=(
                "Check status của volume ở core: nếu status của volume dưới core đang không phải in-use "
                "thì -> gọi CMP API GET volume (/admin-api/volumes/{id}/) để sync lại trạng thái -> sau khi "
                "trạng thái volume đã map giữa core và CMP thì gọi CMP API recreate server "
                "(/admin-api/servers/{id}/recreate/). Nếu status volume dưới core đúng là đang in-use thì "
                "notify cho admin xử lý."
            ),
            server_id="b2c3d4e5-9a01-4f2a-8b3c-1d2e3f4a5b02",
            execution_id="d4e5f607-1b12-4a3b-9c4d-2e3f4a5b6c02",
            signature_template=(
                "ERROR celery.server_creator server_creator_execution {execution_id} "
                "Error generating server {server_id}: Invalid volume: Invalid input received: Invalid volume: "
                "Volume {volume_id} status must be available or downloading to reserve, but the current "
                "status is in-use."
            ),
            template_fields={"volume_id": "c3d4e5f6-2c23-4b4c-ad5e-3f4a5b6c7d02"},
            dependent=("volume", "c3d4e5f6-2c23-4b4c-ad5e-3f4a5b6c7d02"),
        ),
        Scenario(
            id="port_status_drift",
            kb_id="port_status_drift",
            title="Port status drift",
            cause="The port Nova tried to attach is stuck 'in-use' in core (e.g. from a previous attach attempt).",
            instruction=(
                "Check status của port ở core: nếu status của port dưới core đang không phải in-use thì -> "
                "gọi CMP API GET port (/admin-v2/network/elastic-ips/{elastic_ip_id}/) hoặc "
                "(/admin-v2/network/private-ips/{private_ip_id}/) để sync lại trạng thái -> sau khi trạng "
                "thái port đã map giữa core và CMP thì gọi CMP API recreate server "
                "(/admin-api/servers/{id}/recreate/). Nếu status port dưới core đúng là đang in-use thì "
                "notify cho admin xử lý."
            ),
            server_id="c3d4e5f6-3d34-4c5d-8e6f-4a5b6c7d8e03",
            execution_id="f6071829-4e45-4d6e-9f7a-5b6c7d8e9f03",
            signature_template=(
                "ERROR celery.server_creator server_creator_execution {execution_id} "
                "Error generating server {server_id}: Port {port_id} is still in use. "
                "(HTTP 409) (Request-ID: {request_id}) @timestamp:{observed_at}."
            ),
            template_fields={
                "port_id": "e5f60718-4a45-4d6e-9f7a-5b6c7d8e9f04",
                "request_id": "req-a1b2c3d4-5e6f-4708-9a1b-2c3d4e5f6a05",
            },
            dependent=("port", "e5f60718-4a45-4d6e-9f7a-5b6c7d8e9f04"),
        ),
    )
}
