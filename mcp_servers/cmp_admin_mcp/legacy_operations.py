"""Hand-defined legacy operations, not present in the current OpenAPI spec.

`get_volume_legacy` was the CSV troubleshooting compilation's volume-status
check (`/admin-api/volumes/{id}/`). No current equivalent has been located
anywhere in `block_storage.json` -- the current spec only has backup-policy
and volume-QoS endpoints, no plain volume GET at all. This is a stopgap for a
genuine gap in the current API, not a deprecated alternative to something
that already exists in it (server's old `recreate_server` reference was the
latter, and was removed entirely once confirmed no such endpoint ever
existed): confirm this endpoint still exists on a given CMP deployment
before relying on it, and replace this module once the real current
endpoint, if one exists, is found.
"""

from __future__ import annotations

from mcp_servers.openapi_bridge import OperationSpec, ParamSpec

GET_VOLUME_LEGACY = OperationSpec(
    operation_id="get_volume_legacy",
    method="GET",
    path="/admin-api/volumes/{volume_id}/",
    summary="Get Volume (legacy)",
    description=(
        "Legacy volume status/detail lookup from the CSV troubleshooting "
        "compilation. No current equivalent has been located in the block-"
        "storage admin API at all -- treat this as an unverified stopgap, "
        "not a confirmed-working endpoint."
    ),
    parameters=(
        ParamSpec(
            name="volume_id",
            location="path",
            required=True,
            schema={"type": "string", "format": "uuid", "title": "Volume Id"},
        ),
    ),
    body_schema=None,
    body_required=False,
)

LEGACY_OPERATIONS: dict[str, OperationSpec] = {
    GET_VOLUME_LEGACY.operation_id: GET_VOLUME_LEGACY,
}

__all__ = ["GET_VOLUME_LEGACY", "LEGACY_OPERATIONS"]
