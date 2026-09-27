"""Shared constants for the exercise app -- SSH access to the OpenStack VM and
the local ports it tunnels the OpenStack API onto. Same host/key already used
throughout this project's manual testing (ops/openstack_testing/run_tests.py)."""

from pathlib import Path

SSH_HOST = "117.1.29.171"
SSH_PORT = 6123
SSH_USER = "root"
SSH_KEY = str(Path.home() / ".ssh" / "openstack_vm")

# OpenStack API credentials/endpoints -- previously pulled at runtime from
# cmp-mcp's mcp_connections DB (the 'openstack-ops' row), which made this
# app unusable anywhere that DB row didn't exist (e.g. a copy on another
# device). Hardcoded here instead so this app has no dependency on cmp-mcp
# at all beyond the SSH key above.
OS_ENV = {
    "OS_AUTH_HOST": "117.1.29.174",
    "OS_AUTH_PORT": "5000",
    "OS_AUTH_PROTOCOL": "http",
    "OS_IDENTITY_API_VERSION": "3",
    "OS_USERNAME": "admin",
    "OS_PASSWORD": "WkVZ0renEwLi0Z9JQb55MGsOGbAH4RnJCNSFycf1",
    "OS_PROJECT_NAME": "admin",
    "OS_PROJECT_DOMAIN_NAME": "Default",
    "OS_USER_DOMAIN_NAME": "Default",
    "OS_REGION_NAME": "RegionOne",
    "OS_COMPUTE_PORT": "8774",
    "OS_NETWORK_PORT": "9696",
    "OS_VOLUME_PORT": "8776",
    "OS_IMAGE_PORT": "9292",
    "OS_PLACEMENT_PORT": "8780",
    "OS_HEAT_STACK_PORT": "8004",
}

# local port -> remote port, all forwarded to the VM's own real IP (not the
# HAProxy VIP, which isn't internally routable on this single-node box for
# an unrelated reason documented in the Learn OpenStack tab).
TUNNEL_PORTS = {
    5000: 5000,   # keystone
    8774: 8774,   # nova
    9696: 9696,   # neutron
    9292: 9292,   # glance
    8780: 8780,   # placement
    8004: 8004,   # heat
}

HERE = Path(__file__).resolve().parent.parent
CACHE_DIR = HERE / "cache"
STATE_FILE = HERE / "progress_state.json"
CIRROS_URL = "https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img"
CIRROS_PATH = CACHE_DIR / "cirros-0.6.2-x86_64-disk.img"

EXERCISE_KEYPAIR_NAME = "ex-key"
EXERCISE_PUBKEY_PATH = Path.home() / ".ssh" / "openstack_vm.pub"
