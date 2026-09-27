"""Tiny local persistence for which exercises are marked complete and which
one-time warnings have already been acknowledged. A single JSON file is
plenty for a single-user learning tool."""

import json
import threading

from . import config

_lock = threading.Lock()


def _load() -> dict:
    if not config.STATE_FILE.exists():
        return {"completed": [], "warned": [], "position": 0}
    data = json.loads(config.STATE_FILE.read_text())
    data.setdefault("position", 0)
    return data


def _save(data: dict) -> None:
    config.STATE_FILE.write_text(json.dumps(data, indent=2))


def get_state() -> dict:
    with _lock:
        return _load()


def mark_completed(exercise_id: str) -> None:
    with _lock:
        data = _load()
        if exercise_id not in data["completed"]:
            data["completed"].append(exercise_id)
        _save(data)


def mark_warned(exercise_id: str) -> None:
    with _lock:
        data = _load()
        if exercise_id not in data["warned"]:
            data["warned"].append(exercise_id)
        _save(data)


def set_position(index: int) -> None:
    with _lock:
        data = _load()
        data["position"] = index
        _save(data)


def reset() -> None:
    with _lock:
        _save({"completed": [], "warned": [], "position": 0})
