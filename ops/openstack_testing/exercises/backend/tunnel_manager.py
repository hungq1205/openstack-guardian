"""Owns the SSH local-port-forward tunnel to the OpenStack VM for the
lifetime of this app -- starts it on app startup, watches it, restarts it if
it dies, tears it down on shutdown. Exists so the exercise app "just works"
without a human re-running the manual ssh -L dance every session."""

import socket
import subprocess
import threading
import time

from . import config

_process: subprocess.Popen | None = None
_lock = threading.Lock()
_stop = threading.Event()
_monitor_thread: threading.Thread | None = None


def _build_command() -> list[str]:
    cmd = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
           "-p", str(config.SSH_PORT), "-i", config.SSH_KEY]
    for local_port, remote_port in config.TUNNEL_PORTS.items():
        cmd += ["-L", f"{local_port}:{config.SSH_HOST}:{remote_port}"]
    cmd.append(f"{config.SSH_USER}@{config.SSH_HOST}")
    return cmd


def _port_open(port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def is_up() -> bool:
    """True once at least the keystone port answers through the tunnel."""
    return _port_open(5000)


def _spawn() -> None:
    global _process
    with _lock:
        if _process is not None and _process.poll() is None:
            return
        _process = subprocess.Popen(
            _build_command(),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )


def _monitor_loop() -> None:
    while not _stop.is_set():
        if _process is None or _process.poll() is not None:
            _spawn()
        time.sleep(5)


def start() -> None:
    global _monitor_thread
    _spawn()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()


def stop() -> None:
    _stop.set()
    with _lock:
        if _process is not None and _process.poll() is None:
            _process.terminate()
            try:
                _process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _process.kill()
