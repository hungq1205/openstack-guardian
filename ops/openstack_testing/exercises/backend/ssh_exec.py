"""One-off remote command execution over the same SSH key used for the tunnel.
Used by exercises that need to touch the VM directly (Docker container
restarts) rather than going through the OpenStack API."""

import subprocess

from . import config


def run(remote_command: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Runs one command on the OpenStack VM. Returns (ok, combined output)."""
    try:
        result = subprocess.run(
            [
                "ssh", "-p", str(config.SSH_PORT), "-i", config.SSH_KEY,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=8",
                f"{config.SSH_USER}@{config.SSH_HOST}",
                remote_command,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
