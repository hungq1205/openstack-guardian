"""Authenticated openstacksdk connection through the local tunnel, using the
credentials hardcoded in config.OS_ENV -- this app has no runtime
dependency on cmp-mcp beyond the SSH key it uses for the tunnel, so it
works the same wherever it's copied to.

Endpoint overrides are required (not just OS_AUTH_HOST) because Keystone's
service catalog on this cluster points every service at the HAProxy VIP,
which isn't reachable through this tunnel -- same reasoning as
ops/openstack_testing/check_baseline.py in the cmp-mcp repo this app
originated from.
"""

import copy

import openstack
import openstack.exceptions

from . import config


def get_conn() -> "openstack.connection.Connection":
    env = copy.deepcopy(config.OS_ENV)
    env["OS_AUTH_HOST"] = "127.0.0.1"  # via the local tunnel

    return openstack.connect(
        auth_url=f"{env['OS_AUTH_PROTOCOL']}://{env['OS_AUTH_HOST']}:{env['OS_AUTH_PORT']}/v3",
        username=env["OS_USERNAME"],
        password=env["OS_PASSWORD"],
        project_name=env["OS_PROJECT_NAME"],
        user_domain_name=env["OS_USER_DOMAIN_NAME"],
        project_domain_name=env["OS_PROJECT_DOMAIN_NAME"],
        compute_endpoint_override=f"http://127.0.0.1:{env['OS_COMPUTE_PORT']}/v2.1",
        network_endpoint_override=f"http://127.0.0.1:{env['OS_NETWORK_PORT']}/",
        image_endpoint_override=f"http://127.0.0.1:{env['OS_IMAGE_PORT']}/",
    )


def _dedupe_by_name(items, name: str, delete_fn) -> object | None:
    """Neither Glance nor Nova enforce unique names -- two overlapping Init
    calls (or two exercises reusing a name) can leave more than one resource
    answering to the same name, which turns every later name-based lookup
    into a DuplicateResource crash. Deterministically keep the oldest and
    delete the rest, so lookups stay safe without the caller needing to
    know this ever happened."""
    matches = sorted((i for i in items if i.name == name), key=lambda i: i.created_at)
    if not matches:
        return None
    keep, extras = matches[0], matches[1:]
    for extra in extras:
        delete_fn(extra.id)
    return keep


def find_instance(conn, name: str):
    return _dedupe_by_name(
        conn.compute.servers(name=name),
        name,
        lambda sid: conn.compute.delete_server(sid, force=True, ignore_missing=True),
    )


def find_unique_image(conn, name: str):
    return _dedupe_by_name(
        conn.image.images(name=name),
        name,
        conn.image.delete_image,
    )


def delete_instance_if_exists(conn, name: str, wait: bool = True) -> None:
    server = find_instance(conn, name)
    if server is not None:
        conn.compute.delete_server(server, force=True)
        if wait:
            try:
                conn.compute.wait_for_delete(server, timeout=60)
            except Exception:  # noqa: BLE001
                pass
