#!/usr/bin/env bash
# Starts the VM-creation exercises app. Fully standalone -- no dependency on
# the cmp-mcp repo; only needs `uv` and an SSH key at ~/.ssh/openstack_vm
# with access to the OpenStack VM (see backend/config.py for exact
# host/port/user, and backend/config.py's OS_ENV for the OpenStack
# credentials -- both hardcoded there, nothing to configure separately).
#
# Binds 0.0.0.0 so other devices on your LAN can reach it too, at
# http://<this-machine's-LAN-IP>:8910 -- there's no authentication on this
# app, so only run it on networks you trust.
set -e
cd "$(dirname "$0")"
uv run \
    --with "fastapi>=0.115" --with "uvicorn[standard]>=0.30" --with pydantic \
    --with requests --with openstacksdk --with websockets \
    uvicorn backend.app:app --host 0.0.0.0 --port 8910
